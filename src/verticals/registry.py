"""Allowlisted executable adapters referenced by vertical manifests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
from typing import Any

from src.verticals.manifest import ManifestValidationError, PROJECT_ROOT


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
    return partial(validate_schema_mapping, manifest=manifest)


def get_prompt(prompt_path: str) -> str:
    """Read a path already validated by the manifest or shared-prompt loader."""

    try:
        text = Path(prompt_path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ManifestValidationError(f"Could not read prompt {prompt_path!r}.") from exc
    if not text:
        raise ManifestValidationError(f"Empty prompt {prompt_path!r}.")
    return text


def get_shared_prompt(name: str) -> str:
    """Load one named, model-facing template from the central prompt directory."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ManifestValidationError(f"Unsafe shared prompt name {name!r}.")
    root = (PROJECT_ROOT / "prompts/shared").resolve()
    candidate = (root / f"{name}.md").resolve()
    if not root.is_relative_to(PROJECT_ROOT) or not candidate.is_relative_to(root):
        raise ManifestValidationError(f"Unsafe shared prompt {name!r}.")
    return get_prompt(str(candidate))


def get_evaluation_tools(manifest, labelled_root):
    manifest.require_capability("evaluation")
    if manifest.adapter("evaluator") != "private_health_labelled_v1":
        raise ManifestValidationError("Unregistered labelled evaluator.")
    from src.evaluation.metrics import ExtractionEvaluator, PrivateHealthGroundTruthStore
    from src.evaluation.reporter import EvaluationReporter
    return PrivateHealthGroundTruthStore(labelled_root), ExtractionEvaluator(), EvaluationReporter()
