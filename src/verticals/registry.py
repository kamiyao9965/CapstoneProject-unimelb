"""Allowlisted executable adapters referenced by vertical manifests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.verticals.manifest import ManifestValidationError


AcquisitionAdapter = Callable[..., Any]


def get_acquisition_adapter(adapter_id: str) -> AcquisitionAdapter:
    if adapter_id == "travel_public_documents_v1":
        from src.scraper.travel import run_travel_acquisition

        return run_travel_acquisition
    raise ManifestValidationError(
        f"Unregistered acquisition adapter {adapter_id!r}. "
        "Manifest files may reference only allowlisted adapter IDs."
    )
