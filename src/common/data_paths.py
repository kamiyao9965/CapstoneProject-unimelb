from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT_ENV = "KONKRD_DATA_ROOT"


def default_konkrd_data_root() -> Path:
    configured = os.getenv(DATA_ROOT_ENV)
    if configured:
        return Path(configured).expanduser()

    bundled = PROJECT_ROOT / "konkrd-data"
    if bundled.exists():
        return bundled

    for parent in PROJECT_ROOT.parents:
        candidate = parent / "konkrd-data"
        if candidate.exists():
            return candidate

    return PROJECT_ROOT / "konkrd-data"


def default_private_health_pdf_root() -> Path:
    return default_konkrd_data_root() / "data" / "private_health" / "raw" / "PDFs"
