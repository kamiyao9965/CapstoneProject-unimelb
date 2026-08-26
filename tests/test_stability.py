from __future__ import annotations

import unittest
import json

from src.stability.compare import jaccard
from src.stability.signature import signature_from_artifact, signature_from_text
from src.common.json_artifacts import build_success_artifact
from tests.test_json_contracts import VALID_DISCOVERED_SCHEMA


def schema(field_type: str, description: str = "Product name") -> str:
    data = {
        "vertical": "private_health", "version": "0.1-draft",
        "description": "Schema", "product_types": ["hospital"],
        "fields": [{
            "name": "product_name", "type": field_type,
            "description": description, "applies_to": ["hospital"],
            "required": True, "values": [], "aliases": [],
            "enum_ref": None, "item_fields": [], "unique_items": False,
        }],
        "hospital_categories": [], "extras_services": [], "notes": [],
    }
    artifact = build_success_artifact(
        artifact_type="discovered_schema", contract_version="1.0.0",
        data=data,
        provenance={
            "run_id": "test", "provider": "openai", "model": "gpt-5",
            "document_input": "pdf", "source_documents": [], "source_artifacts": [],
        },
        data_contract="private_health/discovered_schema",
    )
    return json.dumps(artifact)


class SemanticSchemaSignatureTest(unittest.TestCase):
    def test_same_name_with_different_type_is_contract_drift(self) -> None:
        first = signature_from_text(schema("string"), "first")
        second = signature_from_text(schema("number"), "second")

        stability, core, union = jaccard(
            [first.field_contracts, second.field_contracts]
        )

        self.assertEqual(first.fields, second.fields)
        self.assertEqual(stability, 0.0)
        self.assertEqual(core, set())
        self.assertEqual(len(union), 2)

    def test_description_change_is_contract_drift(self) -> None:
        first = signature_from_text(schema("string", "Original"), "first")
        second = signature_from_text(schema("string", "Clarified"), "second")

        stability, _, _ = jaccard([first.field_contracts, second.field_contracts])

        self.assertEqual(stability, 0.0)

    def test_envelope_provenance_does_not_change_semantic_signature(self) -> None:
        provenance = {
            "run_id": "one", "provider": "openai", "model": "gpt-5",
            "document_input": "pdf", "source_documents": [], "source_artifacts": [],
        }
        first = build_success_artifact(
            artifact_type="discovered_schema", contract_version="1.0.0",
            data=VALID_DISCOVERED_SCHEMA, provenance=provenance,
            data_contract="private_health/discovered_schema",
            created_at="2026-07-14T00:00:00Z",
        )
        second = json.loads(json.dumps(first))
        second["created_at"] = "2026-07-14T01:00:00Z"
        second["provenance"]["run_id"] = "two"

        self.assertEqual(
            signature_from_artifact(first, "first").field_contracts,
            signature_from_artifact(second, "second").field_contracts,
        )


if __name__ == "__main__":
    unittest.main()
