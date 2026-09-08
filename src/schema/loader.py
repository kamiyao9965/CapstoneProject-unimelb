"""Read current or historical JSON schemas through the shared validation boundary."""
from pathlib import Path

from src.common.json_artifacts import read_artifact
from src.common.json_codec import loads_json
from src.schema.canonical import is_canonical_schema, validate_canonical_schema
from src.schema.validation import validate_schema_mapping
from src.verticals.manifest import VerticalManifest


def load_schema_data(path: str | Path, manifest: VerticalManifest | None = None) -> dict[str, object]:
    source = Path(path)
    payload = loads_json(source.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "artifact_type" in payload:
        payload = read_artifact(source, expected_type="discovered_schema")["data"]
    if is_canonical_schema(payload):
        schema = validate_canonical_schema(payload)
        if manifest is not None and schema["vertical"] != manifest.vertical:
            raise ValueError("Canonical Schema vertical does not match manifest.")
        return schema
    return validate_schema_mapping(payload, manifest=manifest)
