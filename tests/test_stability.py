from __future__ import annotations

import unittest

from src.stability.compare import jaccard
from src.stability.signature import signature_from_text


def schema(field_type: str, description: str = "Product name") -> str:
    return f"""
vertical: private_health
version: 0.1-draft
product_types: [hospital]
fields:
  - name: product_name
    type: {field_type}
    description: {description}
    applies_to: [hospital]
    required: true
    values: []
hospital_categories: []
extras_services: []
"""


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

    def test_consensus_metadata_does_not_change_field_contract(self) -> None:
        base = schema("string")
        annotated = base.replace(
            "    values: []",
            "    values: []\n    consensus:\n      decision: core\n      frequency: 5/5",
        )

        first = signature_from_text(base, "first")
        second = signature_from_text(annotated, "second")

        self.assertEqual(first.field_contracts, second.field_contracts)


if __name__ == "__main__":
    unittest.main()
