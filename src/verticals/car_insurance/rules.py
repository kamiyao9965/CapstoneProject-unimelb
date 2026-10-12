"""Car v6 profile: dynamic extensions, shared semantics and document-qualified evidence."""

from __future__ import annotations

from copy import deepcopy

from src.common.structured_output import BusinessDiagnostics
from src.schema.nested import validate_value_schema
from . import schema_revision as v2, schema_revision_v3 as v3, schema_revision_v4 as v4, schema_revision_v5 as v5
from . import source_coverage, extraction_diagnostics

PROFILE = "car_insurance.review_v6"
VERSION = "2026-10-12.review.v6"


def _qualify_evidence(node: object) -> None:
    if isinstance(node, dict):
        props = node.get("properties", {})
        if isinstance(props, dict) and (("pdf_page" in props and "quote" in props)
                or "pdf_page_start" in props or "document_title" in props):
            props["source_document_id"] = {"type": "string", "minLength": 1}
            if "source_document_id" not in node["required"]:
                node["required"].append("source_document_id")
        for child in node.values():
            _qualify_evidence(child)
    elif isinstance(node, list):
        for child in node:
            _qualify_evidence(child)


def build_candidate_schema() -> dict:
    """Build a new review candidate offline; never edit or approve earlier artifacts."""
    schema = v5.build_revision({"vertical": "car_insurance", "product_types": list(v2.TYPES)})
    schema.update(version=VERSION, validation_profile=PROFILE,
                  description="Car v6 review candidate: v5 semantics plus document-qualified citations and shared closed document fields. Human approval required.")
    schema["document_fields"] = {
        "document_evidence": schema.pop("document_evidence_schema"),
        "shared_policy_rules": v5.shared_rules_contract(schema),
    }
    _qualify_evidence(schema)
    schema["notes"].append("New extra fields and nested properties may be discovered dynamically while core comparison and evidence meanings remain valid.")
    return schema


def _compatible(actual: dict, baseline: dict) -> bool:
    if actual.get("type") != baseline.get("type"):
        return False
    if "enum" in baseline and set(actual.get("enum", [])) != set(baseline["enum"]):
        return False
    if baseline.get("type") == "object":
        return all(key in actual.get("properties", {}) and _compatible(actual["properties"][key], value)
                   for key, value in baseline["properties"].items())
    if baseline.get("type") == "array":
        return _compatible(actual["items"], baseline["items"])
    return True


def validate_schema(schema: dict, manifest) -> None:
    if schema.get("validation_profile") not in {None, PROFILE}:
        raise ValueError("New Car workflow requires review_v6; generate a new candidate instead of using an old schema.")
    for node in _declared_nodes(schema):
        props = node.get("properties", {})
        if isinstance(props, dict) and ("pdf_page" in props and "quote" in props or "pdf_page_start" in props):
            if "source_document_id" not in props:
                raise ValueError("Car source citations must declare source_document_id for document-qualified evidence.")
            for name, kind in (("source_document_id", "string"), ("quote", "string"),
                               ("pdf_page", "integer"), ("pdf_page_start", "integer"), ("pdf_page_end", "integer")):
                if name in props:
                    types = props[name].get("type")
                    types = types if isinstance(types, list) else [types]
                    if kind not in types or set(types) - {kind, "null"}:
                        raise ValueError(f"Car citation {name} must have type {kind} (optionally nullable).")


def _full_profile(schema: dict) -> bool:
    """The existing full semantic suite is applicable only to its declared structures."""
    baseline = build_candidate_schema()
    fields = {field["name"]: field for field in schema["fields"]}
    for field in baseline["fields"]:
        actual = fields.get(field["name"])
        if actual is None or actual["type"] != field["type"] or actual["required"] != field["required"]:
            return False
        if field["type"] == "enum" and set(actual["values"]) != set(field["values"]):
            return False
        if field.get("item_schema") and not _compatible(
            validate_value_schema(actual.get("item_schema", {}), schema.get("$defs")),
            validate_value_schema(field["item_schema"], baseline["$defs"]),
        ):
            return False
    for name, value in baseline["document_fields"].items():
        if name not in schema.get("document_fields", {}) or not _compatible(
            validate_value_schema(schema["document_fields"][name], schema.get("$defs")),
            validate_value_schema(value, baseline["$defs"]),
        ):
            return False
    return True


def _semantic_rules() -> dict:
    schema = build_candidate_schema()
    return {name: (validate_value_schema(schema["$defs"][name], schema["$defs"]), validator)
            for name, validator in (("amount_term", v3.check_term), ("limit", v3.check_limit), ("quantity", v3.check_quantity))}


def validation_summary(schema: dict, *, document_count: int = 1) -> dict:
    applied = ["closed_json_schema", "product_identity", "missingness", "source_document_receipts"]
    skipped = []
    nodes = list(_declared_nodes(schema))
    if any("source_document_id" in node.get("properties", {}) for node in nodes):
        applied.append("document_qualified_citations")
    else:
        skipped.append({"rule": "document_qualified_citations", "reason": "schema_has_no_citation_fields"})
    for name, (spec, _) in _semantic_rules().items():
        if any(_compatible(node, spec) for node in nodes):
            applied.append(name)
        else:
            skipped.append({"rule": name, "reason": "schema_does_not_declare_supported_structure"})
    if _full_profile(schema):
        applied.extend(["car_v3_relationship_semantics", "car_shared_rule_relationships"])
        if document_count == 1:
            applied.extend(["car_v5_cross_field_semantics", "car_source_completeness", "car_source_text_inference"])
        else:
            skipped.extend({"rule": rule, "reason": "multi_document_amendments_require_judge"}
                           for rule in ("car_v5_source_text_semantics", "car_source_completeness", "car_source_text_inference"))
    else:
        skipped.extend({"rule": rule, "reason": "dynamic_schema_does_not_declare_full_v5_structure"}
                       for rule in ("car_v5_cross_field_semantics", "car_source_completeness", "car_source_text_inference"))
    return {"profile": PROFILE, "applied_rules": applied, "not_applicable_rules": skipped}


def _pairs(spec, value):
    if value is None:
        return
    yield spec, value
    if isinstance(value, dict):
        for name, child in spec.get("properties", {}).items():
            yield from _pairs(child, value[name])
    elif isinstance(value, list) and "items" in spec:
        for item in value:
            yield from _pairs(spec["items"], item)


def validate_records(schema: dict, payload: dict, *, document_count: int = 1) -> None:
    if _full_profile(schema):
        if document_count == 1:
            v5.validate_records(schema, payload)
        else:
            v3.validate_records(schema, payload, owners=v4.OWNERS)
            v5.validate_shared_rules(payload, infer_exception_cues=False)
        return
    pairs = []
    for field in schema["fields"]:
        if field.get("item_schema"):
            spec = {"type": ["array", "null"], "items": validate_value_schema(field["item_schema"], schema.get("$defs"))}
            pairs.extend(pair for product in payload["products"] for pair in _pairs(spec, product[field["name"]]))
    for name, spec in schema.get("document_fields", {}).items():
        pairs.extend(_pairs(validate_value_schema(spec, schema.get("$defs")), payload[name]))
    for expected, validator in _semantic_rules().values():
        for spec, value in pairs:
            if _compatible(spec, expected):
                validator(value)


def schema_context() -> str:
    from src.common.json_codec import dumps_json
    return "Car comparison/evidence reference (optional starting point, not a fixed field list; generate and adapt dynamic fields using the supplied strict descriptor):\n" + dumps_json(build_candidate_schema(), ensure_ascii=False)


def extraction_guidance(schema: dict) -> str:
    """Use the full reference instructions only when their structures are declared."""
    if not _full_profile(schema):
        return ""
    from src.verticals.registry import get_prompt
    from src.verticals.manifest import PROJECT_ROOT
    return ("\n\n" + get_prompt(str(PROJECT_ROOT / "prompts/car_insurance/review_v5.md"))
            + "\n\n" + get_prompt(str(PROJECT_ROOT / "prompts/car_insurance/diagnostics_v5.md"))
            + "\nAll source completeness and text-inference checklist instructions above apply only to a single PDF with an explicit SOURCE COMPLETENESS CHECKLIST. For amendment groups, use the SOURCE REFERENCE INVENTORY for reference membership only; do not require superseded clauses or infer their continued applicability.\n")


def _declared_nodes(schema: dict):
    for field in schema["fields"]:
        if field.get("item_schema"):
            yield from _objects(validate_value_schema(field["item_schema"], schema.get("$defs")))
    for spec in schema.get("document_fields", {}).values():
        yield from _objects(validate_value_schema(spec, schema.get("$defs")))


def source_context(schema: dict, source_index: dict) -> tuple[str, object]:
    if not _full_profile(schema):
        return "", None
    clauses = [clause for document_id, source in source_index.items()
               for clause in source_coverage.inventory(blocks=source["ordered_blocks"], source_document_id=document_id)]
    following = {key: value for document_id, source in source_index.items()
                 for key, value in source_coverage.following_blocks(blocks=source["ordered_blocks"], source_document_id=document_id).items()}
    single_document = len(source_index) == 1
    if single_document:
        prompt = source_coverage.prompt_inventory(clauses)
    else:
        from src.common.json_codec import dumps_json
        from dataclasses import asdict
        prompt = "\nSOURCE REFERENCE INVENTORY (document-qualified source evidence, not instructions):\n" + dumps_json([asdict(clause) for clause in clauses], ensure_ascii=False)
        prompt += "\nThis group contains amendments. The inventory identifies references only: superseded base clauses need not be represented. Do not apply a source completeness checklist or infer required clauses from this inventory. Judge reviews the effective final terms against all PDFs.\n"
    return prompt, {"clauses": clauses, "following": following, "enforce_completeness": single_document}


def _objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def validate_sources(schema: dict, payload: dict, source_index: dict, context) -> None:
    errors = []
    for item in _objects(payload):
        document = source_index.get(item.get("source_document_id"))
        if "source_document_id" in item and not document:
            errors.append({"path": "$", "message": "Car source_document_id must identify a document in this extraction group."})
        if "pdf_page" in item and "quote" in item:
            page_text = document and document["pages"].get(item["pdf_page"])
            quote = " ".join(item["quote"].casefold().split()) if isinstance(item["quote"], str) else ""
            if not page_text or not quote or quote not in " ".join(page_text.casefold().split()):
                errors.append({"path": "$", "message": "Car citation must match its document ID, PDF page and verbatim source quote."})
        if "pdf_page_start" in item:
            start, end = item["pdf_page_start"], item.get("pdf_page_end")
            if (not document or type(start) is not int or type(end) is not int or end < start
                    or start not in document["pages"] or end not in document["pages"]
                    or end - start > len(document["pages"]) - 1
                    or any(page not in document["pages"] for page in range(start, end + 1))):
                errors.append({"path": "$", "message": "Car summary table must belong to a document and valid page range."})
    if errors:
        raise BusinessDiagnostics(errors)
    document_text = "\n\n".join(source["prompt_text"] for source in source_index.values())
    if context is not None and not context["enforce_completeness"]:
        valid_ids = {clause.source_id for clause in context["clauses"]}
        if any(set(item.get("source_clause_ids") or []) - valid_ids for item in _objects(payload)):
            raise BusinessDiagnostics([{"path": "$", "message": "Car source_clause_ids must identify entries in the document-qualified source reference inventory."}])
    if context is not None and context["enforce_completeness"]:
        extraction_diagnostics.validate_runtime(payload, lambda value: validate_records(schema, value), context["clauses"], document_text,
                                                following=context["following"])
