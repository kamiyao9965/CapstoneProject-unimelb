"""Resolve pet-insurance PDFs into product-oriented extraction units."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from src.schema.sampler import load_document_manifest


@dataclass(frozen=True)
class ProductFamily:
    family_id: str
    documents: tuple[dict[str, object], ...]
    pdf_paths: tuple[Path, ...]
    expected_products: tuple[dict[str, object], ...]
    unattached_documents: tuple[dict[str, object], ...] = ()

    @property
    def expected_product_ids(self) -> list[str]:
        return [
            str(product["product_id"])
            for product in self.expected_products
            if isinstance(product.get("product_id"), str)
            and str(product["product_id"]).strip()
        ]


def resolve_product_families(
    manifest_path: str | Path,
    pdf_paths: Iterable[str | Path],
) -> list[ProductFamily]:
    """Group selected PDFs by manifest family and inventory known products.

    Every selected PDF must be represented by the manifest.  Silently treating
    an Update/SPDS as an independent product would recreate the exact
    document-vs-product bug this grouping layer is intended to prevent.
    """
    payload = load_document_manifest(manifest_path)
    manifest_file = Path(manifest_path).resolve()
    source_root = _source_root(payload, manifest_file)
    by_resolved_path: dict[Path, dict[str, object]] = {}
    by_name: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    for item in payload["documents"]:
        if not isinstance(item, dict):
            continue
        resolved = _resolve_source_path(str(item["source_path"]), source_root, manifest_file)
        by_resolved_path[resolved] = item
        by_name[resolved.name].append(item)

    grouped: defaultdict[str, list[tuple[dict[str, object], Path]]] = defaultdict(list)
    missing: list[str] = []
    for value in pdf_paths:
        path = Path(value).resolve()
        item = by_resolved_path.get(path)
        if item is None:
            name_matches = by_name.get(path.name, [])
            if len(name_matches) == 1:
                item = name_matches[0]
        if item is None:
            missing.append(path.as_posix())
            continue
        grouped[str(item["document_family_id"])].append((item, path))
    if missing:
        raise ValueError(
            "Pet product-oriented batch requires every PDF in the manifest; missing: "
            + ", ".join(missing)
        )

    families: list[ProductFamily] = []
    for family_id, pairs in sorted(grouped.items()):
        families.extend(_partition_family(family_id, pairs))
    return families


def select_manifest_product_paths(
    manifest_path: str | Path,
    product_ids: Iterable[str],
) -> list[Path]:
    """Resolve a deterministic targeted rerun set from manifest product IDs.

    All base documents declaring a requested product and every amendment linked
    to those bases are returned.  For a shared multi-plan booklet this naturally
    reruns the whole booklet, because its plans must be interpreted together.
    """
    requested = list(dict.fromkeys(
        value.strip()
        for value in product_ids
        if isinstance(value, str) and value.strip()
    ))
    if not requested:
        raise ValueError("At least one non-empty product ID is required.")
    payload = load_document_manifest(manifest_path)
    manifest_file = Path(manifest_path).resolve()
    source_root = _source_root(payload, manifest_file)
    documents = [
        document for document in payload["documents"] if isinstance(document, dict)
    ]
    documents_by_id = {
        str(document["document_id"]): document for document in documents
    }
    seeds: set[str] = set()
    found_products: set[str] = set()
    for document in documents:
        for product in _declared_products(document):
            product_id = str(product.get("product_id") or "").strip()
            if product_id in requested:
                found_products.add(product_id)
                seeds.add(str(document["document_id"]))
    missing = [
        product_id for product_id in requested if product_id not in found_products
    ]
    if missing:
        raise ValueError("Unknown manifest product_id value(s): " + ", ".join(missing))

    adjacency: dict[str, set[str]] = {
        document_id: set() for document_id in documents_by_id
    }
    for document_id, document in documents_by_id.items():
        for amended_id_value in document.get("amends_document_ids", []):
            amended_id = str(amended_id_value)
            if amended_id in documents_by_id:
                adjacency[document_id].add(amended_id)
                adjacency[amended_id].add(document_id)
    selected_ids = set(seeds)
    pending = list(seeds)
    while pending:
        current = pending.pop()
        new_ids = adjacency[current] - selected_ids
        selected_ids.update(new_ids)
        pending.extend(new_ids)

    selected_paths: list[Path] = []
    for document in documents:
        if str(document["document_id"]) not in selected_ids:
            continue
        path = _resolve_source_path(
            str(document["source_path"]), source_root, manifest_file
        )
        if not path.is_file():
            raise FileNotFoundError(path)
        selected_paths.append(path)
    return selected_paths


def _partition_family(
    family_id: str,
    pairs: list[tuple[dict[str, object], Path]],
) -> list[ProductFamily]:
    """Build independently extractable document components within one family.

    ``document_family_id`` is a broad grouping used for sampling and review; it
    is not itself evidence that every base PDS describes the same product.
    Components are therefore joined only by an explicit amendment link, or when
    two base documents explicitly declare the same product ID. A multi-plan PDS
    remains one component so it can still emit several products in one request.
    """
    pairs_by_id = {str(document["document_id"]): (document, path) for document, path in pairs}
    base_ids = {
        document_id
        for document_id, (document, _) in pairs_by_id.items()
        if str(document.get("document_role") or "")
        not in {"update", "supplementary_pds"}
    }
    if not base_ids:
        return []

    adjacency: dict[str, set[str]] = {document_id: set() for document_id in pairs_by_id}
    for document_id, (document, _) in pairs_by_id.items():
        if str(document.get("document_role") or "") not in {"update", "supplementary_pds"}:
            continue
        for amended_id in document.get("amends_document_ids", []):
            amended_id = str(amended_id)
            if amended_id in pairs_by_id:
                adjacency[document_id].add(amended_id)
                adjacency[amended_id].add(document_id)

    # Separate base files occasionally form one product from several documents.
    # The manifest's stable product ID, unlike family ID, is an explicit signal
    # that these files should be considered together.
    base_by_product_id: defaultdict[str, list[str]] = defaultdict(list)
    for document_id in base_ids:
        document, _ = pairs_by_id[document_id]
        for product in _declared_products(document):
            product_id = str(product.get("product_id") or "").strip()
            if product_id:
                base_by_product_id[product_id].append(document_id)
    for document_ids in base_by_product_id.values():
        for document_id in document_ids[1:]:
            adjacency[document_ids[0]].add(document_id)
            adjacency[document_id].add(document_ids[0])

    components = _connected_components(adjacency)
    active_components = [component for component in components if component.intersection(base_ids)]
    unattached = [
        pairs_by_id[document_id][0]
        for component in components
        if not component.intersection(base_ids)
        for document_id in component
    ]

    families: list[ProductFamily] = []
    for index, component in enumerate(sorted(active_components, key=lambda ids: min(ids))):
        component_pairs = [pairs_by_id[document_id] for document_id in component]
        component_pairs.sort(key=lambda pair: _document_sort_key(pair[0]))
        families.append(ProductFamily(
            family_id=family_id,
            documents=tuple(pair[0] for pair in component_pairs),
            pdf_paths=tuple(pair[1] for pair in component_pairs),
            expected_products=tuple(_component_products(component_pairs)),
            # Keep the warning visible once per broad family, rather than once
            # for every independently extracted product component.
            unattached_documents=tuple(unattached) if index == 0 else (),
        ))
    return families


def _connected_components(adjacency: Mapping[str, set[str]]) -> list[set[str]]:
    remaining = set(adjacency)
    components: list[set[str]] = []
    while remaining:
        start = remaining.pop()
        component = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            neighbours = adjacency[current].intersection(remaining)
            remaining.difference_update(neighbours)
            component.update(neighbours)
            pending.extend(neighbours)
        components.append(component)
    return components


def _declared_products(document: Mapping[str, object]) -> list[dict[str, object]]:
    declared = document.get("products") or []
    if not isinstance(declared, list):
        return []
    return [dict(product) for product in declared if isinstance(product, Mapping)]


def _component_products(
    pairs: Iterable[tuple[dict[str, object], Path]],
) -> list[dict[str, object]]:
    products: list[dict[str, object]] = []
    seen_product_ids: set[str] = set()
    for document, _ in pairs:
        for product in _declared_products(document):
            product_id = str(product.get("product_id") or "").strip()
            if product_id and product_id in seen_product_ids:
                continue
            if product_id:
                seen_product_ids.add(product_id)
            products.append(product)
    return products


def family_prompt_context(family: ProductFamily) -> str:
    """Render manifest routing metadata without presenting hints as policy facts."""
    document_lines = []
    for item in family.documents:
        document_lines.append(
            "- "
            f"document_id={item['document_id']}; role={item['document_role']}; "
            f"date={item.get('document_date') or 'unknown'}; "
            f"amends={item.get('amends_document_ids') or []}; "
            f"source_path={item['source_path']}"
        )
    product_lines = []
    for product in family.expected_products:
        product_lines.append(
            "- "
            f"product_id={product.get('product_id')}; "
            f"name_hint={product.get('product_name_hint')}; "
            f"cover_scope_hint={product.get('cover_scope')}"
        )
    inventory = "\n".join(product_lines) if product_lines else "- unknown; discover products from the PDFs"
    return (
        f"Document family: {family.family_id}\n"
        "Documents (routing metadata):\n"
        + "\n".join(document_lines)
        + "\nExpected product inventory (identity hints only, not evidence for benefits):\n"
        + inventory
    )


def _source_root(payload: Mapping[str, object], manifest_file: Path) -> Path:
    value = payload.get("source_root")
    if isinstance(value, str) and value.strip():
        path = Path(value)
        if path.is_absolute():
            return path.resolve()
        cwd_candidate = path.resolve()
        if cwd_candidate.exists():
            return cwd_candidate
        return (manifest_file.parent / path).resolve()
    return manifest_file.parent


def _resolve_source_path(value: str, source_root: Path, manifest_file: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    cwd_candidate = path.resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    root_candidate = (source_root / path).resolve()
    if root_candidate.exists():
        return root_candidate
    return (manifest_file.parent / path).resolve()


def _document_sort_key(document: Mapping[str, object]) -> tuple[str, str, str]:
    role = str(document.get("document_role") or "")
    # Base terms first, amendments later, so both prompt order and the model's
    # precedence reasoning follow the product lifecycle.
    role_order = "1" if role in {"update", "supplementary_pds"} else "0"
    return role_order, str(document.get("document_date") or ""), str(document["document_id"])
