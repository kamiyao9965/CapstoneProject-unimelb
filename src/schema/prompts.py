"""Compatibility prompt exports; text is owned by each manifest package."""
from src.verticals.manifest import default_manifest_path, load_vertical_manifest
from src.verticals.registry import get_prompt

SCHEMA_DISCOVERY_PROMPT = get_prompt(load_vertical_manifest(default_manifest_path("private_health")).prompt("discovery"))
SCHEMA_PATCH_PROMPT = get_prompt(load_vertical_manifest(default_manifest_path("private_health")).prompt("patch"))
TRAVEL_INSURANCE_SCHEMA_DISCOVERY_PROMPT = get_prompt(load_vertical_manifest(default_manifest_path("travel_insurance")).prompt("discovery"))
TRAVEL_INSURANCE_SCHEMA_PATCH_PROMPT = get_prompt(load_vertical_manifest(default_manifest_path("travel_insurance")).prompt("patch"))
