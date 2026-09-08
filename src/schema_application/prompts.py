"""Compatibility prompt exports; text is owned by each manifest package."""
from src.verticals.manifest import default_manifest_path, load_vertical_manifest
from src.verticals.registry import get_prompt

EXTRACTION_PROMPT = get_prompt(load_vertical_manifest(default_manifest_path("private_health")).prompt("extraction"))
TRAVEL_INSURANCE_EXTRACTION_PROMPT = get_prompt(load_vertical_manifest(default_manifest_path("travel_insurance")).prompt("extraction"))
