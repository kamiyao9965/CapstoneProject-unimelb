"""Allowlisted executable adapters referenced by vertical manifests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.verticals.manifest import ManifestValidationError


AcquisitionAdapter = Callable[..., Any]
SchemaValidator = Callable[[object], dict[str, object]]


def get_acquisition_adapter(adapter_id: str) -> AcquisitionAdapter:
    if adapter_id == "travel_public_documents_v1":
        from src.scraper.travel import run_travel_acquisition

        return run_travel_acquisition
    raise ManifestValidationError(
        f"Unregistered acquisition adapter {adapter_id!r}. "
        "Manifest files may reference only allowlisted adapter IDs."
    )


def get_schema_validator(manifest) -> SchemaValidator:
    from functools import partial
    from src.schema.validation import validate_schema_mapping
    from src.verticals.manifest import VerticalManifest, default_manifest_path, load_vertical_manifest

    if not isinstance(manifest, VerticalManifest):
        # Temporary call-site compatibility, removed after consumers migrate.
        vertical = str(manifest).removesuffix("_schema_v1")
        manifest = load_vertical_manifest(default_manifest_path(vertical))
    return partial(validate_schema_mapping, manifest=manifest)


def get_prompt(prompt_path: str) -> str:
    """Read the package path already validated by VerticalManifest.prompt."""
    from pathlib import Path

    try:
        text = Path(prompt_path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ManifestValidationError(f"Could not read prompt {prompt_path!r}.") from exc
    if not text:
        raise ManifestValidationError(f"Empty prompt {prompt_path!r}.")
    return text


def get_evaluation_tools(manifest, labelled_root):
    manifest.require_capability("evaluation")
    if manifest.adapter("evaluator") != "private_health_labelled_v1":
        raise ManifestValidationError("Unregistered labelled evaluator.")
    from src.evaluation.metrics import ExtractionEvaluator, PrivateHealthGroundTruthStore
    from src.evaluation.reporter import EvaluationReporter
    return PrivateHealthGroundTruthStore(labelled_root), ExtractionEvaluator(), EvaluationReporter()
