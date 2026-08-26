from __future__ import annotations

import json
import random
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.evaluation.metrics import PrivateHealthGroundTruthStore


class EvaluationManifestEntry(BaseModel):
    source_relative_path: str
    source_sha256: str
    id_master: str
    component_id_masters: list[str] = Field(default_factory=list)
    product_type: str


class EvaluationManifest(BaseModel):
    version: str = "1.0"
    seed: int
    input_root: str
    entries: list[EvaluationManifestEntry]

    @classmethod
    def load(cls, path: str | Path) -> "EvaluationManifest":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def write(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return output

    def resolve_paths(self, input_root: str | Path) -> list[Path]:
        root = Path(input_root).resolve()
        paths: list[Path] = []
        for entry in self.entries:
            candidate = (root / entry.source_relative_path).resolve()
            if not candidate.is_relative_to(root):
                raise ValueError(
                    f"Manifest path escapes input root: {entry.source_relative_path}"
                )
            if not candidate.is_file():
                raise FileNotFoundError(candidate)
            actual_hash = file_sha256(candidate)
            if actual_hash != entry.source_sha256:
                raise ValueError(
                    f"Manifest hash mismatch for {entry.source_relative_path}: "
                    f"expected {entry.source_sha256}, got {actual_hash}"
                )
            paths.append(candidate)
        return paths

    def entry_for(self, pdf_path: str | Path, input_root: str | Path) -> EvaluationManifestEntry:
        relative = Path(pdf_path).resolve().relative_to(Path(input_root).resolve()).as_posix()
        matches = [entry for entry in self.entries if entry.source_relative_path == relative]
        if len(matches) != 1:
            raise KeyError(f"No unique manifest entry for {relative}")
        return matches[0]


def build_evaluation_manifest(
    *,
    input_root: str | Path,
    ground_truth_store: PrivateHealthGroundTruthStore,
    count: int,
    seed: int,
) -> tuple[EvaluationManifest, dict[str, Any]]:
    """Build a reproducible set containing only exact, unambiguous GT matches."""
    root = Path(input_root).resolve()
    candidates: list[EvaluationManifestEntry] = []
    rejected: dict[str, int] = {}
    seen_hashes: set[str] = set()

    for pdf_path in sorted(root.rglob("*.pdf")):
        source_hash = file_sha256(pdf_path)
        if source_hash in seen_hashes:
            rejected["duplicate_content"] = rejected.get("duplicate_content", 0) + 1
            continue
        match = ground_truth_store.match_pdf(pdf_path)
        if match is None:
            rejected["unmatched"] = rejected.get("unmatched", 0) + 1
            continue
        selected = next(
            (
                item for item in match.candidate_matches
                if str(item.get("id_master")) in set(match.component_id_masters or [match.id_master])
            ),
            None,
        )
        if not selected or not selected.get("match_evidence", {}).get("strict_pdf_filepath"):
            rejected["not_strict_path"] = rejected.get("not_strict_path", 0) + 1
            continue
        if match.low_confidence_match or match.ambiguous_match:
            rejected["low_confidence_or_ambiguous"] = rejected.get(
                "low_confidence_or_ambiguous", 0
            ) + 1
            continue
        _, ground_truth = ground_truth_store.load_ground_truth_for_ids(
            pdf_path,
            match.component_id_masters or [match.id_master],
        )
        if not ground_truth:
            rejected["no_scored_ground_truth"] = rejected.get("no_scored_ground_truth", 0) + 1
            continue
        seen_hashes.add(source_hash)
        candidates.append(
            EvaluationManifestEntry(
                source_relative_path=pdf_path.relative_to(root).as_posix(),
                source_sha256=source_hash,
                id_master=match.id_master,
                component_id_masters=match.component_id_masters,
                product_type=match.product_type,
            )
        )

    if len(candidates) < count:
        raise ValueError(
            f"Only {len(candidates)} exact, unambiguous, unique PDFs are eligible; "
            f"cannot build a {count}-document manifest"
        )
    rng = random.Random(seed)
    rng.shuffle(candidates)
    selected = sorted(candidates[:count], key=lambda entry: entry.source_relative_path)
    return (
        EvaluationManifest(
            seed=seed,
            input_root=root.as_posix(),
            entries=selected,
        ),
        {"eligible": len(candidates), "selected": len(selected), "rejected": rejected},
    )


def file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
