from __future__ import annotations

import copy
import unittest

from src.common import model_provider
from src.common.json_contracts import ContractValidationError, validate_contract, validate_inline_contract
from src.schema.contract import compile_extraction_contract
from src.verticals.manifest import resolve_manifest


def wire_node(node_id: str, node_type: str, **overrides) -> dict[str, object]:
    node = {
        "node_id": node_id, "type": node_type, "nullable": False,
        "description": f"Synthetic {node_id} definition.", "properties": [], "items_node_id": "",
        "enum_values": [], "minimum": None, "maximum": None, "min_length": None, "max_length": None,
    }
    node.update(overrides)
    return node


def generation_payload(vertical: str = "travel_insurance", *, property_name: str = "benefit_name") -> dict[str, object]:
    manifest = resolve_manifest(vertical=vertical)
    product_type = "domestic" if vertical == "travel_insurance" else "hospital"
    return {
        "vertical": vertical, "version": "generation-regression-v2",
        "description": "Synthetic dynamic nested schema.", "product_types": [product_type],
        "taxonomies": {name: [] for name in manifest.taxonomies}, "notes": [],
        "validation_profile": "", "document_fields": [],
        "fields": [
            {"name": "product_type", "type": "enum", "description": "Classification.",
             "applies_to": [product_type], "required": True, "values": [product_type], "item_node_id": ""},
            {"name": "product_name", "type": "string", "description": "Published product name.",
             "applies_to": [product_type], "required": True, "values": [], "item_node_id": ""},
            {"name": "benefits", "type": "list[object]", "description": "Exact benefit definitions.",
             "applies_to": [product_type], "required": False, "values": [], "item_node_id": "benefit"},
        ],
        "schema_nodes": [
            wire_node("benefit", "object", properties=[
                {"name": property_name, "node_id": "name"},
                {"name": "limit", "node_id": "limit"},
                {"name": "evidence", "node_id": "evidence"},
            ]),
            wire_node("name", "string", nullable=True, min_length=1, max_length=80),
            wire_node("limit", "object", nullable=True, properties=[
                {"name": "amount_aud", "node_id": "amount"}, {"name": "period", "node_id": "period"},
            ]),
            wire_node("amount", "number", nullable=True, minimum=0, maximum=100000),
            wire_node("period", "string", nullable=True, enum_values=["per_year", "per_claim"]),
            wire_node("evidence", "array", nullable=True, items_node_id="source"),
            wire_node("source", "object", properties=[
                {"name": "document_id", "node_id": "document_id"}, {"name": "page", "node_id": "page"},
            ]),
            wire_node("document_id", "string", min_length=1),
            wire_node("page", "integer", nullable=True, minimum=1),
        ],
    }


def generation_patch_payload(vertical: str = "travel_insurance") -> dict[str, object]:
    schema = generation_payload(vertical)
    return {
        "patches": [{
            "patch_type": "add_field", "target_group": f"{schema['product_types'][0]}_cover",
            "field_name": "new_benefits", "canonical_name": "new_benefits", "type": "list[object]",
            "description": "New exact benefit structure.", "applies_to": schema["product_types"],
            "required": False, "values": [], "evidence_documents": [], "confidence": 0.9,
            "rationale": "Explicit benefit structure in the source.", "item_node_id": "benefit",
        }],
        "schema_nodes": schema["schema_nodes"],
    }


def generation_api():
    from src.schema import generation

    return generation


class GenerationWireTests(unittest.TestCase):
    def test_fixed_wire_contracts_project_for_openai_strict_and_anthropic(self) -> None:
        api = generation_api()
        for vertical in ("private_health", "travel_insurance"):
            manifest = resolve_manifest(vertical=vertical)
            for patches in (False, True):
                with self.subTest(vertical=vertical, patches=patches):
                    wire = generation_patch_payload(vertical) if patches else generation_payload(vertical)
                    contract = api.generation_contract(manifest, patches=patches)
                    validate_inline_contract(wire, contract)
                    projected = model_provider._project_openai_schema(contract, strict=True)
                    validate_inline_contract(wire, projected)
                    anthropic = model_provider._project_anthropic_schema(contract)
                    validate_inline_contract(wire, anthropic)
                    self.assertLessEqual(model_provider._count_schema_key(anthropic, "anyOf"), 16)
                    self.assertEqual(model_provider._count_optional_properties(anthropic), 0)

    def test_wire_contracts_do_not_accept_native_item_schema_or_extra_node_keys(self) -> None:
        api = generation_api()
        manifest = resolve_manifest(vertical="travel_insurance")
        for defect in ("native_item_schema", "extra_node_key"):
            with self.subTest(defect=defect):
                wire = generation_payload()
                contract = api.generation_contract(manifest)
                validate_inline_contract(wire, contract)
                if defect == "native_item_schema":
                    wire["fields"][-1]["item_schema"] = {"type": "object"}
                else:
                    wire["schema_nodes"][0]["undeclared"] = True
                with self.assertRaises(ContractValidationError):
                    validate_inline_contract(wire, contract)

    def test_dynamic_property_names_decode_into_closed_nested_contracts(self) -> None:
        api = generation_api()
        for vertical in ("private_health", "travel_insurance"):
            manifest = resolve_manifest(vertical=vertical)
            for name in ("benefit_name", "marketed_cover_label"):
                with self.subTest(vertical=vertical, property_name=name):
                    wire = generation_payload(vertical, property_name=name)
                    original = copy.deepcopy(wire)
                    decoded = api.decode_generated_schema(wire, manifest)
                    self.assertEqual(wire, original)
                    self.assertNotIn("schema_nodes", decoded)
                    fields = {field["name"]: field for field in decoded["fields"]}
                    self.assertNotIn("item_node_id", fields["benefits"])
                    item = fields["benefits"]["item_schema"]
                    self.assertEqual(set(item["properties"]), {name, "limit", "evidence"})
                    self.assertEqual(set(item["required"]), set(item["properties"]))
                    self.assertIs(item["additionalProperties"], False)
                    self.assertEqual(item["properties"]["limit"]["properties"]["amount_aud"]["minimum"], 0)
                    self.assertEqual(item["properties"][name]["minLength"], 1)
                    record = {
                        "product_name": "Example plan", "product_type": wire["product_types"][0],
                        "benefits": [{name: "Cover label", "limit": {"amount_aud": None, "period": None},
                                      "evidence": [{"document_id": "pds_A", "page": None}]}],
                        "_unfilled": [], "_notes": None,
                    }
                    payload = ({"products": [record], "_document_notes": None}
                               if manifest.documents.output_cardinality == "multiple" else record)
                    validate_inline_contract(payload, compile_extraction_contract(decoded, manifest=manifest))
                    record["benefits"][0]["evidence"][0]["page"] = "3"
                    with self.assertRaises(ContractValidationError):
                        validate_inline_contract(payload, compile_extraction_contract(decoded, manifest=manifest))

    def test_patch_wire_decodes_to_native_patch_artifact_format(self) -> None:
        api = generation_api()
        for vertical in ("private_health", "travel_insurance"):
            with self.subTest(vertical=vertical):
                wire = generation_patch_payload(vertical)
                original = copy.deepcopy(wire)
                decoded = api.decode_generated_patches(wire, resolve_manifest(vertical=vertical))
                self.assertEqual(wire, original)
                self.assertNotIn("schema_nodes", decoded)
                self.assertNotIn("item_node_id", decoded["patches"][0])
                self.assertIn("item_schema", decoded["patches"][0])
                validate_contract(decoded, "schema_refinement/candidate_patch_set")

    def test_duplicate_missing_cyclic_and_unused_node_ids_are_rejected(self) -> None:
        api = generation_api()
        manifest = resolve_manifest(vertical="travel_insurance")
        for defect in ("duplicate", "missing", "remote_reference", "cycle", "unused", "duplicate_property"):
            with self.subTest(defect=defect):
                wire = generation_payload()
                if defect == "duplicate":
                    wire["schema_nodes"].append(copy.deepcopy(wire["schema_nodes"][0]))
                elif defect in {"missing", "remote_reference"}:
                    wire["schema_nodes"][0]["properties"][0]["node_id"] = (
                        "missing" if defect == "missing" else "https://example.invalid/schema.json"
                    )
                elif defect == "cycle":
                    wire["schema_nodes"][0]["properties"][0]["node_id"] = "benefit"
                elif defect == "unused":
                    wire["schema_nodes"].append(wire_node("unused", "boolean"))
                else:
                    wire["schema_nodes"][0]["properties"].append(
                        copy.deepcopy(wire["schema_nodes"][0]["properties"][0])
                    )
                original = copy.deepcopy(wire)
                with self.assertRaises(ValueError):
                    api.decode_generated_schema(wire, manifest)
                self.assertEqual(wire, original)

    def test_node_type_configuration_conflicts_are_rejected_without_guessing(self) -> None:
        api = generation_api()
        manifest = resolve_manifest(vertical="travel_insurance")
        for defect in ("scalar_properties", "array_without_items", "string_numeric_bound", "number_string_bound",
                       "wrong_enum_type", "non_object_list_item", "scalar_item_reference"):
            with self.subTest(defect=defect):
                wire = generation_payload()
                nodes = {node["node_id"]: node for node in wire["schema_nodes"]}
                if defect == "scalar_properties":
                    nodes["name"]["properties"] = [{"name": "invented", "node_id": "page"}]
                elif defect == "array_without_items":
                    nodes["evidence"]["items_node_id"] = ""
                elif defect == "string_numeric_bound":
                    nodes["name"]["minimum"] = 0
                elif defect == "number_string_bound":
                    nodes["amount"]["min_length"] = 1
                elif defect == "wrong_enum_type":
                    nodes["period"]["enum_values"] = [100]
                elif defect == "non_object_list_item":
                    wire["fields"][-1]["item_node_id"] = "name"
                else:
                    wire["fields"][1]["item_node_id"] = "benefit"
                with self.assertRaises(ValueError):
                    api.decode_generated_schema(wire, manifest)

    def test_patch_decoder_rejects_bad_references_and_unused_nodes(self) -> None:
        api = generation_api()
        manifest = resolve_manifest(vertical="travel_insurance")
        for defect in ("missing", "cycle", "unused"):
            with self.subTest(defect=defect):
                wire = generation_patch_payload()
                if defect == "missing":
                    wire["patches"][0]["item_node_id"] = "missing"
                elif defect == "cycle":
                    wire["schema_nodes"][0]["properties"][0]["node_id"] = "benefit"
                else:
                    wire["schema_nodes"].append(wire_node("unused", "string"))
                with self.assertRaises(ValueError):
                    api.decode_generated_patches(wire, manifest)


if __name__ == "__main__":
    unittest.main()
