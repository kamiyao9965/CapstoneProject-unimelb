from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.model_config import resolve_selection
from src.common.json_artifacts import (
    build_failure_artifact,
    build_success_artifact,
    write_artifact,
    write_failure_artifact,
)
from src.common.structured_output import StructuredOutputFailure
from src.schema.discovery import SchemaDiscovery
from src.schema.sampler import DEFAULT_CATEGORIES, print_samples, select_samples


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a private health schema from PDFs")
    parser.add_argument("--samples", nargs="+")
    parser.add_argument("--input-root", default="data/private_health/raw/PDFs")
    parser.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    parser.add_argument("--per-category", type=int, default=5)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--document-input")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--keep-uploaded-files", action="store_true")
    parser.add_argument("--output", default="outputs/private_health/schema.json")
    parser.add_argument("--usage-log", default="outputs/private_health/token_usage.jsonl")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        selection = resolve_selection(
            provider=args.provider,
            model=args.model,
            document_input=args.document_input,
        )
    except ValueError as exc:
        parser.error(str(exc))
    categories = tuple(category.lower() for category in args.categories)
    input_root = Path(args.input_root)
    output_path = next_available_path(Path(args.output))
    run_id = uuid4().hex
    sample_paths = list(args.samples or [])

    try:
        if not sample_paths:
            sample_paths = select_samples(
                input_root=input_root,
                categories=categories,
                per_category=args.per_category,
                seed=args.seed,
            )
        if not args.samples:
            print_samples(sample_paths, input_root, categories)

        schema_data = SchemaDiscovery(
            selection=selection,
            cleanup_uploaded_files=not args.keep_uploaded_files,
            timeout_seconds=args.timeout,
            usage_log_path=args.usage_log,
            pdf_root=input_root,
        ).discover(sample_paths, output_path=output_path, run_id=run_id)
    except Exception as exc:
        details = (
            list(exc.result.errors)
            if isinstance(exc, StructuredOutputFailure)
            else []
        )
        failure = build_failure_artifact(
            artifact_type="schema_discovery_error",
            contract_version="1.0.0",
            provenance={
                "run_id": run_id,
                "provider": selection.provider,
                "model": selection.model,
                "document_input": selection.document_input,
                "source_documents": list(sample_paths),
                "source_artifacts": [],
            },
            error_code=(
                "structured_output_exhausted"
                if isinstance(exc, StructuredOutputFailure)
                else "schema_discovery_failed"
            ),
            message=str(exc),
            details=details,
        )
        try:
            error_path = (
                output_path.parent / "errors" / "schema_discovery" / f"{run_id}.json"
            )
            if not error_path.exists():
                error_path = write_failure_artifact(
                    output_path.parent, "schema_discovery", run_id, failure
                )
            print(f"Failure artifact: {error_path}")
        except Exception as artifact_exc:
            print(f"Could not write failure artifact: {artifact_exc}")
        print(f"Schema discovery failed: {exc}")
        return 1

    artifact = build_success_artifact(
        artifact_type="discovered_schema",
        contract_version="1.0.0",
        data=schema_data,
        provenance={
            "run_id": run_id,
            "provider": selection.provider,
            "model": selection.model,
            "document_input": selection.document_input,
            "source_documents": list(sample_paths),
            "source_artifacts": [],
        },
        data_contract="private_health/discovered_schema",
    )
    write_artifact(
        output_path,
        artifact,
        data_contract="private_health/discovered_schema",
    )
    print(f"Wrote schema draft to {output_path}")
    return 0


def next_available_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find an available output path for {path}")


if __name__ == "__main__":
    raise SystemExit(main())
