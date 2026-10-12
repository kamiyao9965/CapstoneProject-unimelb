"""Resolve extraction tasks only from explicit, validated amendment links."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

from src.common.json_codec import dumps_json
from src.pdf_ingestion.cache import sha256_file
from src.verticals.manifest import VerticalManifest


AMENDMENT_ROLES = frozenset({"spds", "update", "supplementary_pds"})


@dataclass(frozen=True)
class DocumentReference:
    document_id: str
    source_path: str
    document_role: str
    amends_document_ids: tuple[str, ...]
    sha256: str
    metadata: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {"document_id": self.document_id, "source_path": self.source_path,
                "document_role": self.document_role, "amends_document_ids": list(self.amends_document_ids),
                "sha256": self.sha256, "metadata": deepcopy(dict(self.metadata))}


@dataclass(frozen=True)
class DocumentGroup:
    group_id: str
    documents: tuple[DocumentReference, ...]
    pdf_paths: tuple[Path, ...]

    @property
    def primary_path(self) -> Path:
        return self.pdf_paths[0]

    def provenance(self) -> dict[str, object]:
        return {"document_group_id": self.group_id,
                "source_documents": [str(path) for path in self.pdf_paths],
                "source_document_refs": [document.to_dict() for document in self.documents]}


def document_group_id(documents: Iterable[Mapping[str, object]]) -> str:
    identity = [{"document_id": item["document_id"], "sha256": item["sha256"],
                 "amends_document_ids": list(item["amends_document_ids"])} for item in documents]
    return "group_" + sha256(dumps_json(identity, sort_keys=True).encode()).hexdigest()[:24]


def expand_document_paths(manifest: VerticalManifest, paths: Iterable[str | Path], *, input_root=None) -> list[str]:
    """Report every PDF seen by discovery and reserve them together for holdout."""
    selected = list(paths)
    if not manifest.documents.inventory:
        return [str(path) for path in selected]
    return [str(path) for group in resolve_document_groups(manifest, selected, input_root=input_root)
            for path in group.pdf_paths]


def resolve_document_groups(
    manifest: VerticalManifest,
    pdf_paths: Iterable[str | Path],
    *,
    input_root: str | Path | None = None,
) -> list[DocumentGroup]:
    """Collect complete connected components, keeping parents before amendments."""
    root = Path(input_root or manifest.path("input_root")).resolve()
    selected = {Path(value).resolve() for value in pdf_paths}

    def resolve_path(value: str | Path) -> Path:
        path = Path(value)
        path = path.resolve() if path.is_absolute() else (root / path).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".pdf":
            raise ValueError("Inventory PDF path must stay inside the selected input root (source root).")
        return path

    for path in selected:
        resolve_path(path)
    inventory = manifest.documents.inventory
    if not inventory:
        def declared_role(path: Path) -> str:
            parts = {part.casefold() for part in path.relative_to(root).parts[:-1]}
            roles = parts & (set(manifest.documents.document_types) | AMENDMENT_ROLES)
            if roles & AMENDMENT_ROLES:
                return sorted(roles & AMENDMENT_ROLES)[0]
            return next(iter(roles)) if len(roles) == 1 else "document"
        inventory = tuple({"document_id": "document_" + sha256(str(path.relative_to(root)).encode()).hexdigest()[:24],
                           "source_path": str(path), "document_role": declared_role(path),
                           "amends_document_ids": []} for path in sorted(selected))
    by_id: dict[str, dict[str, object]] = {}
    by_path: dict[Path, str] = {}
    for entry in inventory:
        document_id = entry.get("document_id")
        if not isinstance(document_id, str) or not document_id or document_id in by_id:
            raise ValueError("Inventory document IDs must be non-empty and unique.")
        source = entry.get("source_path")
        if not isinstance(source, str) or not source:
            raise ValueError("Inventory document requires a source_path.")
        path = resolve_path(source)
        if path in by_path:
            raise ValueError("Inventory must not declare one PDF under multiple document IDs.")
        amends = entry.get("amends_document_ids", [])
        if not isinstance(amends, list) or any(not isinstance(value, str) for value in amends) or len(amends) != len(set(amends)):
            raise ValueError("amends_document_ids must be a list of unique document IDs.")
        by_id[document_id], by_path[path] = dict(entry), document_id
    if selected - set(by_path):
        raise ValueError("Selected PDFs must be registered in the configured inventory.")
    parents: dict[str, set[str]] = {}
    adjacent = {document_id: set() for document_id in by_id}
    for document_id, entry in by_id.items():
        parent_ids = set(entry.get("amends_document_ids", []))
        if parent_ids - set(by_id) or document_id in parent_ids:
            raise ValueError("Amendment references must name existing, different documents.")
        parents[document_id] = parent_ids
        for parent_id in parent_ids:
            adjacent[document_id].add(parent_id)
            adjacent[parent_id].add(document_id)
    ordered: list[str] = []
    remaining = set(by_id)
    while remaining:
        ready = sorted(document_id for document_id in remaining if not parents[document_id].intersection(remaining))
        if not ready:
            raise ValueError("Amendment references must not contain cycles.")
        ordered.extend(ready)
        remaining.difference_update(ready)
    pending_ids = {by_path[path] for path in selected}
    groups: list[DocumentGroup] = []
    while pending_ids:
        component = {min(pending_ids)}
        pending = list(component)
        while pending:
            new = adjacent[pending.pop()] - component
            component.update(new)
            pending.extend(new)
        pending_ids.difference_update(component)
        documents = []
        paths = []
        for document_id in ordered:
            if document_id not in component:
                continue
            entry = by_id[document_id]
            role = str(entry.get("document_role") or "document")
            if role.casefold() in AMENDMENT_ROLES and not parents[document_id]:
                raise ValueError("Unlinked SPDS/Update requires review; it cannot be extracted as an independent product.")
            path = resolve_path(str(entry["source_path"]))
            actual_hash = sha256_file(path)
            if entry.get("sha256") and entry["sha256"] != actual_hash:
                raise ValueError("Inventory PDF hash does not match its declared source.")
            metadata = entry.get("metadata", {})
            if not isinstance(metadata, Mapping):
                raise ValueError("Inventory document metadata must be an object.")
            documents.append(DocumentReference(document_id, str(path), role, tuple(sorted(parents[document_id])),
                                               actual_hash, MappingProxyType(deepcopy(dict(metadata)))))
            paths.append(path)
        group_id = document_group_id(item.to_dict() for item in documents)
        groups.append(DocumentGroup(group_id, tuple(documents), tuple(paths)))
    return sorted(groups, key=lambda group: group.primary_path.as_posix())
