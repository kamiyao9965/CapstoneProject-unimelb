"""Read both extraction file formats through one identity boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.common.json_codec import dumps_json, loads_json
from src.common.json_contracts import load_contract, validate_contract, validate_inline_contract
from src.common.model_config import ModelSelection
from src.common.models import ExtractionResult
from src.schema.canonical import (
    compile_canonical_extraction_contract, is_canonical_schema,
    validate_canonical_extraction_identities,
)
from src.schema.contract import compile_extraction_contract
from src.schema.nested import closed_extraction_contract
from src.schema.validation import validate_extraction_record, validate_schema_mapping
from src.verticals.manifest import PROJECT_ROOT, VerticalManifest
from src.verticals.documents import DocumentGroup, document_group_id, resolve_document_groups
from src.pdf_ingestion.cache import sha256_file


@dataclass(frozen=True)
class ParsedExtraction:
    artifact: dict[str, Any]
    data: dict[str, Any]
    source_document: str
    vertical: str | None
    schema_version: str | None
    provider: str | None
    model: str | None
    run_id: str | None
    source_documents: tuple[str, ...] = ()
    source_document_refs: tuple[dict[str, Any], ...] = ()
    document_group_id: str | None = None


def extraction_binding(schema: dict[str, object], manifest: VerticalManifest) -> dict[str, str]:
    """Bind requests and resume validation to the same schema and prompt content."""
    from src.verticals.registry import get_prompt, get_shared_prompt, get_business_rules
    prompt = get_prompt(manifest.prompt("extraction")) + get_shared_prompt("extraction_request")
    rules = get_business_rules(manifest)
    if rules is not None and hasattr(rules, "extraction_guidance"):
        prompt += rules.extraction_guidance(schema)
    return {
        "schema_sha256": hashlib.sha256(dumps_json(schema, sort_keys=True).encode()).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
    }


def parse_extraction_artifact(
    raw: bytes,
    *,
    vertical: str,
    schema_version: str | None = None,
    require_identity: bool = False,
) -> ParsedExtraction:
    """Validate the wrapper and identity; callers validate data against their schema.

    Analysis may read historical envelopes without identity. Storage requires an
    explicit identity. Legacy run IDs retain the hash of the original file bytes.
    """
    artifact = loads_json(raw.decode("utf-8"))
    if not isinstance(artifact, dict):
        raise ValueError("Extraction must be a JSON object.")
    if "artifact_type" in artifact:
        validate_contract(artifact, "artifact_envelope")
        if artifact["status"] != "success" or artifact["artifact_type"] != "extraction_result":
            raise ValueError("Not a successful extraction result artifact.")
        identity = artifact["provenance"]
        source_documents = identity["source_documents"]
        data = artifact["data"]
    else:
        try:
            result = ExtractionResult.model_validate(artifact)
        except ValidationError as exc:
            raise ValueError("Legacy extraction artifact is invalid.") from exc
        identity = result.model_dump()
        identity["run_id"] = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        source_documents = result.source_documents or [result.source_path]
        if source_documents[0] != result.source_path:
            raise ValueError("Extraction primary source does not match its source documents.")
        data = result.data

    if (not source_documents or any(not value.strip() for value in source_documents)
            or len(set(source_documents)) != len(source_documents)):
        raise ValueError("Extraction must identify unique non-empty source documents.")
    refs = identity.get("source_document_refs") or []
    group_id = identity.get("document_group_id")
    if len(source_documents) > 1 or refs or group_id:
        receipt_schema = load_contract("artifact_envelope")["$defs"]["provenance"]["properties"]["source_document_refs"]
        validate_inline_contract(refs, receipt_schema, "source_document_receipt")
        if len(refs) != len(source_documents) or [ref["source_path"] for ref in refs] != source_documents:
            raise ValueError("Extraction source references must bind every document in order.")
        ids = [ref["document_id"] for ref in refs]
        if len(set(ids)) != len(ids) or group_id != document_group_id(refs):
            raise ValueError("Extraction document group identity is invalid.")
        visited: set[str] = set()
        adjacent = {value: set() for value in ids}
        for ref in refs:
            parents = set(ref["amends_document_ids"])
            if not parents.issubset(visited):
                raise ValueError("Extraction amendment references must bind earlier source documents.")
            visited.add(ref["document_id"])
            for parent in parents:
                adjacent[parent].add(ref["document_id"])
                adjacent[ref["document_id"]].add(parent)
        connected, pending = set(), [ids[0]]
        while pending:
            current = pending.pop()
            if current not in connected:
                connected.add(current)
                pending.extend(adjacent[current] - connected)
        if connected != set(ids):
            raise ValueError("Multi-document extraction requires explicit amendment links.")
    for field, expected in (("vertical", vertical), ("schema_version", schema_version)):
        label = field.replace("_", " ")
        if require_identity and not identity.get(field):
            raise ValueError(f"Extraction {label} is missing; regenerate before storage.")
        if expected is not None and field in identity and identity[field] != expected:
            raise ValueError(f"Extraction {label} does not match the selected schema.")

    return ParsedExtraction(
        artifact=artifact, data=data, source_document=source_documents[0],
        vertical=identity.get("vertical"), schema_version=identity.get("schema_version"),
        provider=identity.get("provider"), model=identity.get("model"),
        run_id=identity.get("run_id"),
        source_documents=tuple(source_documents), source_document_refs=tuple(refs), document_group_id=group_id,
    )


def resolve_extraction_sources(
    extraction: ParsedExtraction, manifest: VerticalManifest, *, input_root: str | Path | None = None,
) -> DocumentGroup:
    """Verify the complete configured group and its current bytes before using a result."""
    paths = tuple(
        (Path(value) if Path(value).is_absolute() else PROJECT_ROOT / value).resolve()
        for value in extraction.source_documents
    )
    try:
        groups = resolve_document_groups(manifest, paths, input_root=input_root)
    except OSError as exc:
        raise ValueError("Extraction source PDF is missing or unreadable.") from exc
    if len(groups) != 1 or groups[0].pdf_paths != paths:
        raise ValueError("Extraction must bind one complete configured document group.")
    group = groups[0]
    if extraction.source_document_refs:
        expected = [ref.to_dict() for ref in group.documents]
        actual = [dict(ref, source_path=str(path)) for ref, path in zip(extraction.source_document_refs, paths)]
        if actual != expected or extraction.document_group_id != group.group_id:
            raise ValueError("Extraction document receipts do not match the current inventory and PDF bytes.")
    elif len(group.documents) > 1:
        raise ValueError("Multi-document extraction requires complete source receipts.")
    return group


def load_cached_extraction(
    path: Path, *, pdf_path: Path, schema: dict[str, object],
    manifest: VerticalManifest, selection: ModelSelection, document_parser: str,
    document_group: DocumentGroup | None = None,
    input_root: str | Path | None = None,
) -> ExtractionResult:
    """Validate a batch resume result without creating a provider or changing it."""
    parsed = parse_extraction_artifact(
        path.read_bytes(), vertical=manifest.vertical,
        schema_version=str(schema["version"]), require_identity=True,
    )
    if Path(parsed.source_document).resolve() != pdf_path.resolve():
        raise ValueError("Existing extraction source does not match the selected PDF.")
    identity = parsed.artifact.get("provenance", parsed.artifact)
    if document_group is not None:
        if tuple(Path(path).resolve() for path in parsed.source_documents) != document_group.pdf_paths:
            raise ValueError("Existing extraction source documents do not match the selected group.")
        if len(document_group.documents) > 1 or parsed.source_document_refs:
            if (parsed.document_group_id != document_group.group_id
                    or list(parsed.source_document_refs) != [ref.to_dict() for ref in document_group.documents]):
                raise ValueError("Existing extraction source references do not match the document group.")
        for ref, path in zip(document_group.documents, document_group.pdf_paths):
            if sha256_file(path) != ref.sha256:
                raise ValueError("PDF bytes changed after resolving the document group.")
    elif len(parsed.source_documents) > 1:
        raise ValueError("Multi-document resume requires the complete configured document group.")
    for name, value in extraction_binding(schema, manifest).items():
        if identity.get(name) is not None and identity[name] != value:
            raise ValueError(f"Existing extraction {name} does not match the current configuration.")
    if parsed.provider != selection.provider or parsed.model != selection.model:
        raise ValueError("Existing extraction used a different provider or model.")
    if identity.get("document_parser") != document_parser:
        raise ValueError("Existing extraction used a different or unrecorded PDF parser.")
    if is_canonical_schema(schema):
        if schema["output"]["cardinality"] != manifest.documents.output_cardinality:
            raise ValueError("Canonical Schema output cardinality does not match the manifest.")
        contract = closed_extraction_contract(compile_canonical_extraction_contract(schema))
        validate_inline_contract(parsed.data, contract, "cached_extraction")
        validate_canonical_extraction_identities(schema, parsed.data)
    else:
        normalized = validate_schema_mapping(dict(schema), manifest=manifest)
        contract = compile_extraction_contract(normalized, manifest=manifest)
        validate_inline_contract(parsed.data, contract, "cached_extraction")
        contract = closed_extraction_contract(contract)
        validate_extraction_record(normalized, parsed.data, manifest=manifest,
                                   document_count=len(parsed.source_documents))
    from src.verticals.registry import get_business_rules, business_validation_summary
    rules = get_business_rules(manifest)
    checks = business_validation_summary(manifest, schema, document_count=len(parsed.source_documents))
    if rules is not None:
        rules.validate_schema(schema, manifest)
        if is_canonical_schema(schema):
            rules.validate_records(schema, parsed.data, document_count=len(parsed.source_documents))
        from src.pdf_ingestion.adapter import render_pdf_paths_for_prompt
        group = resolve_extraction_sources(parsed, manifest, input_root=input_root)
        source_index: dict[str, dict] = {}
        render_pdf_paths_for_prompt(group.pdf_paths, document_refs=[ref.to_dict() for ref in group.documents],
                                   source_index=source_index, cache_dir=manifest.path("cache_root"),
                                   document_parser=document_parser,
                                   parser_quality_gate=manifest.adapters.get("parser_quality") == "car_parser_quality_v1",
                                   parser_review_path=manifest.path("parser_review") if "parser_review" in manifest.paths else None)
        _, context = rules.source_context(schema, source_index)
        rules.validate_sources(schema, parsed.data, source_index, context)
    if identity.get("validation_checks") is not None and identity["validation_checks"] != checks:
        raise ValueError("Existing extraction validation checks do not match the current configuration.")
    if "artifact_type" not in parsed.artifact:
        return ExtractionResult.model_validate(parsed.artifact)
    return ExtractionResult(
        vertical=parsed.vertical, schema_version=parsed.schema_version,
        source_path=parsed.source_document, extracted_at=parsed.artifact["created_at"],
        provider=parsed.provider, model=parsed.model, document_parser=document_parser,
        data=parsed.data,
        source_documents=list(parsed.source_documents), source_document_refs=list(parsed.source_document_refs),
        document_group_id=parsed.document_group_id,
        schema_sha256=identity.get("schema_sha256"), prompt_sha256=identity.get("prompt_sha256"),
        validation_checks=checks,
    )
