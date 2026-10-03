"""Build an auditable inventory from explicitly assigned local PDFs (no API calls)."""

from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

SPLITS = {"development", "holdout", "test"}
DOCUMENT_TYPES = {"pds", "spds", "ped", "tmd", "fsg"}
COLUMNS = {
    "relative_path", "insurer", "release_group", "split", "document_type",
    "source_url", "retrieved_at",
}


def build_inventory(input_root: Path, assignments: Path) -> dict:
    """Pin PDF bytes and reject duplicate paths and cross-split source leakage.

    release_group is an explicit human assignment. It must group all related
    PDS/SPDS and near-identical versions; byte hashes cannot detect paraphrases.
    """
    root = input_root.resolve(strict=True)
    entries = []
    seen_paths: set[Path] = set()
    group_splits: dict[tuple[str, str], str] = {}
    hash_splits: dict[str, str] = {}
    with assignments.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if set(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"CSV must contain exactly: {', '.join(sorted(COLUMNS))}")
        for row_number, row in enumerate(reader, 2):
            if None in row or any(not value or not value.strip() for value in row.values()):
                raise ValueError(f"Row {row_number}: all columns must be populated.")
            row = {key: value.strip() for key, value in row.items()}
            split = row["split"]
            if split not in SPLITS or row["document_type"] not in DOCUMENT_TYPES:
                raise ValueError(f"Row {row_number}: unsupported split or document type.")
            relative = Path(row["relative_path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Row {row_number}: relative_path must stay within input root.")
            path = (root / relative).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"Row {row_number}: PDF must stay within input root.")
            if path in seen_paths:
                raise ValueError(f"Row {row_number}: duplicate PDF path.")
            seen_paths.add(path)
            # Match the actual shared sampler's <insurer>/<category> convention.
            expected = (split, row["insurer"], row["document_type"])
            if path.relative_to(root).parts[:3] != expected:
                raise ValueError(f"Row {row_number}: expected split/insurer/document_type/PDF layout.")
            url = urlparse(row["source_url"])
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError(f"Row {row_number}: source_url must be an HTTPS provenance URL without credentials.")
            if date.fromisoformat(row["retrieved_at"]) > date.today():
                raise ValueError(f"Row {row_number}: retrieved_at cannot be in the future.")
            digest = hashlib.sha256()
            with path.open("rb") as pdf:
                if path.suffix.lower() != ".pdf" or pdf.read(5) != b"%PDF-":
                    raise ValueError(f"Row {row_number}: file does not have a PDF signature.")
                pdf.seek(0)
                for block in iter(lambda: pdf.read(1024 * 1024), b""):
                    digest.update(block)
            sha256 = digest.hexdigest()
            group = (row["insurer"], row["release_group"])
            if group_splits.setdefault(group, split) != split:
                raise ValueError(f"Row {row_number}: release group leaks across splits.")
            if hash_splits.setdefault(sha256, split) != split:
                raise ValueError(f"Row {row_number}: identical PDF leaks across splits.")
            entries.append({**row, "relative_path": path.relative_to(root).as_posix(),
                            "sha256": sha256, "size_bytes": path.stat().st_size})
    if not entries:
        raise ValueError("No PDFs registered; fill the intake CSV with real documents first.")
    return {
        "inventory_version": "1.0", "vertical": "car_insurance",
        "input_root": str(root), "entries": sorted(entries, key=lambda item: item["relative_path"]),
        "summary": {
            "documents": len(entries), "unique_pdfs": len(hash_splits),
            "splits": {split: sum(item["split"] == split for item in entries)
                       for split in sorted(SPLITS)},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=Path("data/car_insurance"))
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = build_inventory(args.input_root, args.assignments)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
