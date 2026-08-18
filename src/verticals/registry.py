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


def get_schema_validator(adapter_id: str) -> SchemaValidator:
    if adapter_id == "private_health_schema_v1":
        from src.schema.validation import validate_schema_mapping

        return validate_schema_mapping
    if adapter_id == "travel_insurance_schema_v1":
        from src.verticals.travel_insurance import validate_travel_schema_mapping

        return validate_travel_schema_mapping
    raise ManifestValidationError(f"Unregistered schema validator {adapter_id!r}.")


def get_prompt(prompt_id: str) -> str:
    if prompt_id == "private_health_discovery_v1":
        from src.schema.prompts import SCHEMA_DISCOVERY_PROMPT

        return SCHEMA_DISCOVERY_PROMPT
    if prompt_id == "private_health_extraction_v1":
        from src.schema_application.prompts import EXTRACTION_PROMPT

        return EXTRACTION_PROMPT
    if prompt_id == "travel_insurance_discovery_v1":
        from src.schema.prompts import TRAVEL_INSURANCE_SCHEMA_DISCOVERY_PROMPT

        return TRAVEL_INSURANCE_SCHEMA_DISCOVERY_PROMPT
    if prompt_id == "travel_insurance_extraction_v1":
        from src.schema_application.prompts import TRAVEL_INSURANCE_EXTRACTION_PROMPT

        return TRAVEL_INSURANCE_EXTRACTION_PROMPT
    raise ManifestValidationError(f"Unregistered prompt {prompt_id!r}.")
