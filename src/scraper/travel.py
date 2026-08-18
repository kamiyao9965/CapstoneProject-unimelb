"""Travel-insurance acquisition configuration and run orchestration."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit, urlunsplit

from src.common.json_artifacts import build_success_artifact, write_artifact
from src.scraper.classification import (
    classify_document,
    classify_product_axes,
    parse_effective_date,
    relationship_confidence,
)
from src.scraper.crawler import (
    DiscoveredLink,
    discover_follow_links,
    discover_pdf_links,
)
from src.scraper.downloader import PdfStore, PdfValidationError
from src.scraper.http_client import HttpBodyLimitError, SafeHttpClient
from src.scraper.pdf_inspection import inspect_pdf


_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]*$")
_AXIS_VALUES = {
    "geographic_scopes": {"international", "domestic", "inbound", "unknown"},
    "trip_frequencies": {"single_trip", "annual_multi_trip", "unknown"},
    "plan_tiers": {"comprehensive", "essentials", "basic", "medical_only", "unknown"},
}


@dataclass(frozen=True)
class StartPageConfig:
    url: str
    required_context_hints: tuple[str, ...]
    follow_link_hints: tuple[str, ...]


@dataclass(frozen=True)
class SeedDocumentConfig:
    url: str
    source_page: str
    title: str
    section_heading: str | None
    version_status: str


@dataclass(frozen=True)
class ProviderConfig:
    insurer_code: str
    brand_code: str
    issuer: str | None
    underwriter: str | None
    allowed_domains: tuple[str, ...]
    start_pages: tuple[StartPageConfig, ...]
    seed_documents: tuple[SeedDocumentConfig, ...]
    default_axes: dict[str, list[str]]


@dataclass(frozen=True)
class AcquisitionConfig:
    vertical: str
    contract_version: str
    user_agent: str
    timeout_seconds: float
    request_interval_seconds: float
    retries: int
    max_redirects: int
    max_html_bytes: int
    max_pdf_bytes: int
    max_pdf_pages: int
    sample_pdf_pages: int
    providers: tuple[ProviderConfig, ...]


@dataclass(frozen=True)
class AcquisitionOutcome:
    artifact_path: Path
    pdf_paths: tuple[Path, ...]
    run_id: str
    data: dict[str, Any]


def load_acquisition_config(path: str | Path) -> AcquisitionConfig:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read acquisition config {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Acquisition config must contain a JSON object.")
    _require_keys(
        payload,
        {
            "vertical", "contract_version", "user_agent", "timeout_seconds",
            "request_interval_seconds", "retries", "max_redirects", "max_html_bytes",
            "max_pdf_bytes", "max_pdf_pages", "sample_pdf_pages", "providers",
        },
        "config",
    )
    if payload["vertical"] != "travel_insurance":
        raise ValueError("Acquisition config vertical must be 'travel_insurance'.")
    providers_raw = payload["providers"]
    if not isinstance(providers_raw, list) or not providers_raw:
        raise ValueError("Acquisition config requires at least one provider.")
    providers = tuple(_parse_provider(item) for item in providers_raw)
    codes = [provider.insurer_code for provider in providers]
    duplicate_codes = sorted({code for code in codes if codes.count(code) > 1})
    if duplicate_codes:
        raise ValueError(f"Duplicate insurer_code values: {duplicate_codes}")
    config = AcquisitionConfig(
        vertical="travel_insurance",
        contract_version=_nonempty_string(payload["contract_version"], "contract_version"),
        user_agent=_nonempty_string(payload["user_agent"], "user_agent"),
        timeout_seconds=_positive_float(payload["timeout_seconds"], "timeout_seconds"),
        request_interval_seconds=_nonnegative_float(
            payload["request_interval_seconds"], "request_interval_seconds"
        ),
        retries=_nonnegative_int(payload["retries"], "retries"),
        max_redirects=_nonnegative_int(payload["max_redirects"], "max_redirects"),
        max_html_bytes=_positive_int(payload["max_html_bytes"], "max_html_bytes"),
        max_pdf_bytes=_positive_int(payload["max_pdf_bytes"], "max_pdf_bytes"),
        max_pdf_pages=_positive_int(payload["max_pdf_pages"], "max_pdf_pages"),
        sample_pdf_pages=_positive_int(payload["sample_pdf_pages"], "sample_pdf_pages"),
        providers=providers,
    )
    if config.sample_pdf_pages > config.max_pdf_pages:
        raise ValueError("sample_pdf_pages cannot exceed max_pdf_pages.")
    return config


def run_travel_acquisition(
    *,
    config_path: str | Path,
    data_root: str | Path,
    output_root: str | Path,
    insurer_codes: Sequence[str] = (),
    include_archived: bool = False,
    discovery_only: bool = False,
    http_client: Any | None = None,
    run_id: str | None = None,
) -> AcquisitionOutcome:
    config = load_acquisition_config(config_path)
    selected = _select_providers(config.providers, insurer_codes)
    actual_run_id = run_id or _new_run_id()
    client = http_client or SafeHttpClient(
        user_agent=config.user_agent,
        timeout_seconds=config.timeout_seconds,
        request_interval_seconds=config.request_interval_seconds,
        retries=config.retries,
        max_redirects=config.max_redirects,
    )
    pdf_store = PdfStore(data_root, max_bytes=config.max_pdf_bytes)
    documents: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    pdf_paths: list[Path] = []
    document_text: dict[str, str] = {}
    providers_succeeded = 0

    for provider in selected:
        candidates = _discover_provider(
            provider,
            client,
            max_html_bytes=config.max_html_bytes,
            include_archived=include_archived,
            errors=errors,
        )
        provider_valid_documents = 0
        for candidate in candidates:
            preliminary_type = classify_document(
                candidate.anchor_text,
                candidate.url,
                candidate.context_text,
            )
            if discovery_only:
                documents.append(
                    _undownloaded_document(
                        provider,
                        candidate,
                        preliminary_type,
                        error_code=None,
                    )
                )
                continue
            if not client.robots_allowed(candidate.url, provider.allowed_domains):
                documents.append(
                    _undownloaded_document(
                        provider,
                        candidate,
                        preliminary_type,
                        error_code="robots_disallowed",
                    )
                )
                errors.append(
                    _run_error(
                        provider.insurer_code,
                        "download",
                        candidate.url,
                        "robots_disallowed",
                        "robots.txt does not permit this document request.",
                    )
                )
                continue
            try:
                with client.open_stream(
                    candidate.url,
                    provider.allowed_domains,
                ) as stream:
                    content_length = _header_int(stream.headers, "content-length")
                    if content_length is not None and content_length > config.max_pdf_bytes:
                        raise PdfValidationError(
                            "pdf_size_exceeded",
                            "size_exceeded",
                            "PDF Content-Length exceeds the configured size limit.",
                        )
                    stored = pdf_store.store(
                        stream.iter_chunks(),
                        insurer_code=provider.insurer_code,
                        document_type=preliminary_type,
                        title=_document_title(candidate),
                        content_type=_header(stream.headers, "content-type"),
                    )
                    final_url = stream.final_url
            except PdfValidationError as exc:
                documents.append(
                    _failed_document(provider, candidate, preliminary_type, exc)
                )
                errors.append(
                    _run_error(
                        provider.insurer_code,
                        "download",
                        candidate.url,
                        exc.code,
                        str(exc),
                    )
                )
                continue
            except Exception as exc:
                documents.append(
                    _failed_document(provider, candidate, preliminary_type, exc)
                )
                errors.append(
                    _run_error(
                        provider.insurer_code,
                        "download",
                        candidate.url,
                        "document_download_failed",
                        str(exc) or exc.__class__.__name__,
                    )
                )
                continue

            inspection = inspect_pdf(
                stored.path,
                max_pages=config.max_pdf_pages,
                sample_pages=config.sample_pdf_pages,
            )
            classification_text = " ".join(
                [candidate.context_text, *candidate.evidence, inspection.text]
            )
            document_type = classify_document(
                candidate.anchor_text,
                final_url,
                inspection.text,
            )
            axes = _choose_product_axes(
                classify_product_axes(candidate.context_text),
                classify_product_axes(inspection.text),
                provider.default_axes,
            )
            document_id = f"sha256:{stored.sha256}"
            document = {
                "document_id": document_id,
                "insurer_code": provider.insurer_code,
                "brand_code": provider.brand_code,
                "issuer": provider.issuer,
                "underwriter": provider.underwriter,
                "document_type": document_type,
                "title": _document_title(candidate),
                "source_page": candidate.source_page,
                "discovered_url": candidate.url,
                "final_url": final_url,
                "section_heading": candidate.section_heading,
                "anchor_text": candidate.anchor_text,
                **axes,
                "effective_from": parse_effective_date(classification_text),
                "version_status": candidate.version_status,
                "retrieval_status": stored.retrieval_status,
                "validation_status": stored.validation_status,
                "parse_status": inspection.parse_status,
                "local_path": str(stored.path.resolve()),
                "sha256": stored.sha256,
                "content_type": stored.content_type or None,
                "size_bytes": stored.size_bytes,
                "error_code": inspection.error_code,
                "evidence": list(candidate.evidence),
            }
            _append_or_merge_document(documents, document)
            if len(inspection.text) > len(document_text.get(document_id, "")):
                document_text[document_id] = inspection.text
            provider_valid_documents += 1
            if stored.path not in pdf_paths:
                pdf_paths.append(stored.path)
        if candidates and (discovery_only or provider_valid_documents > 0):
            providers_succeeded += 1

    relationships, review_items = _build_relationships(documents, document_text)
    product_releases = _build_product_releases(documents, relationships)
    data = {
        "vertical": "travel_insurance",
        "run_id": actual_run_id,
        "source_config": str(Path(config_path)),
        "documents": documents,
        "relationships": relationships,
        "product_releases": product_releases,
        "review_items": review_items,
        "errors": errors,
        "summary": {
            "providers_attempted": len(selected),
            "providers_succeeded": providers_succeeded,
            "documents_discovered": len(documents),
            "documents_downloaded": sum(
                item["retrieval_status"] in {"downloaded", "duplicate"}
                for item in documents
            ),
            "valid_pdfs": sum(
                item["validation_status"] == "valid_pdf" for item in documents
            ),
            "failed_documents": sum(
                item["retrieval_status"] == "failed" for item in documents
            ),
            "relationships": len(relationships),
            "review_items": len(review_items),
        },
    }
    source_documents = [page.url for provider in selected for page in provider.start_pages]
    artifact = build_success_artifact(
        artifact_type="travel_insurance_acquisition_run",
        contract_version=config.contract_version,
        data=data,
        provenance={
            "run_id": actual_run_id,
            "provider": None,
            "model": None,
            "document_input": "public_https_pdf",
            "source_documents": source_documents,
            "source_artifacts": [str(Path(config_path))],
        },
        data_contract="travel_insurance/acquisition_run",
    )
    artifact_path = (
        Path(output_root) / actual_run_id / "acquisition.json"
    )
    write_artifact(
        artifact_path,
        artifact,
        data_contract="travel_insurance/acquisition_run",
    )
    return AcquisitionOutcome(
        artifact_path=artifact_path,
        pdf_paths=tuple(path.resolve() for path in pdf_paths),
        run_id=actual_run_id,
        data=data,
    )


def _discover_provider(
    provider: ProviderConfig,
    client: Any,
    *,
    max_html_bytes: int,
    include_archived: bool,
    errors: list[dict[str, Any]],
) -> list[DiscoveredLink]:
    candidates: dict[str, DiscoveredLink] = {
        seed.url: DiscoveredLink(
            url=seed.url,
            source_page=seed.source_page,
            anchor_text=seed.title,
            section_heading=seed.section_heading,
            version_status=seed.version_status,
            evidence=[
                f"configured official document: {seed.title}",
                f"source page: {seed.source_page}",
            ],
            context_evidence=[],
        )
        for seed in provider.seed_documents
        if include_archived or seed.version_status != "archived"
    }
    fetched_pages: set[str] = set()
    for start_page in provider.start_pages:
        pages = [(start_page.url, False)]
        while pages:
            page_url, is_follow = pages.pop(0)
            if page_url in fetched_pages:
                continue
            fetched_pages.add(page_url)
            if not client.robots_allowed(page_url, provider.allowed_domains):
                errors.append(
                    _run_error(
                        provider.insurer_code,
                        "discovery",
                        page_url,
                        "robots_disallowed",
                        "robots.txt does not permit this source-page request.",
                    )
                )
                continue
            try:
                response = client.fetch_bytes(
                    page_url,
                    provider.allowed_domains,
                    max_bytes=max_html_bytes,
                )
                html = response.body.decode("utf-8", errors="replace")
            except HttpBodyLimitError as exc:
                errors.append(
                    _run_error(
                        provider.insurer_code,
                        "discovery",
                        page_url,
                        "html_size_exceeded",
                        str(exc),
                    )
                )
                continue
            except Exception as exc:
                errors.append(
                    _run_error(
                        provider.insurer_code,
                        "discovery",
                        page_url,
                        "source_page_fetch_failed",
                        str(exc) or exc.__class__.__name__,
                    )
                )
                continue
            for candidate in discover_pdf_links(
                html,
                response.final_url,
                required_context_hints=start_page.required_context_hints,
                include_archived=include_archived,
            ):
                _merge_candidate(candidates, candidate)
            if not is_follow:
                for follow_url in discover_follow_links(
                    html,
                    response.final_url,
                    follow_link_hints=start_page.follow_link_hints,
                ):
                    pages.append((follow_url, True))
    return list(candidates.values())


def _merge_candidate(
    candidates: dict[str, DiscoveredLink],
    candidate: DiscoveredLink,
) -> None:
    existing = candidates.get(candidate.url)
    if existing is None:
        candidates[candidate.url] = candidate
        return
    for evidence in candidate.evidence:
        if evidence not in existing.evidence:
            existing.evidence.append(evidence)
    for context in candidate.context_evidence:
        if context not in existing.context_evidence:
            existing.context_evidence.append(context)
    if existing.version_status == "archived" and candidate.version_status == "current":
        existing.version_status = "current"


def _append_or_merge_document(
    documents: list[dict[str, Any]],
    incoming: dict[str, Any],
) -> None:
    existing = next(
        (
            document
            for document in documents
            if document["document_id"] == incoming["document_id"]
        ),
        None,
    )
    if existing is None:
        documents.append(incoming)
        return
    alternate_evidence = [
        f"alternate discovery URL: {incoming['discovered_url']}",
        f"alternate source page: {incoming['source_page']}",
        *incoming["evidence"],
    ]
    for evidence in alternate_evidence:
        if evidence not in existing["evidence"]:
            existing["evidence"].append(evidence)
    for axis in _AXIS_VALUES:
        values = [*existing[axis], *incoming[axis]]
        if any(value != "unknown" for value in values):
            values = [value for value in values if value != "unknown"]
        existing[axis] = list(dict.fromkeys(values)) or ["unknown"]
    if existing["effective_from"] is None and incoming["effective_from"] is not None:
        existing["effective_from"] = incoming["effective_from"]
    if existing["parse_status"] != "parsed" and incoming["parse_status"] == "parsed":
        existing["parse_status"] = "parsed"
        existing["error_code"] = None


def _build_relationships(
    documents: list[dict[str, Any]],
    document_text: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    relationships: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    relation_types = {
        "spds": "supplements",
        "brochure": "summarises",
        "tmd": "targets",
        "fsg": "guides",
    }
    usable = [
        document
        for document in documents
        if document["validation_status"] == "valid_pdf"
        and document["version_status"] == "current"
    ]
    for auxiliary in usable:
        relationship_type = relation_types.get(auxiliary["document_type"])
        if relationship_type is None:
            continue
        pds_candidates = [
            document
            for document in usable
            if document["document_type"] == "pds"
            and document["insurer_code"] == auxiliary["insurer_code"]
            and document["document_id"] != auxiliary["document_id"]
        ]
        if not pds_candidates:
            continue
        scored = [
            (_relationship_score(auxiliary, pds), pds) for pds in pds_candidates
        ]
        best_score = max(score for score, _ in scored)
        best = [pds for score, pds in scored if score == best_score]
        for pds in best:
            same_section = (
                auxiliary["source_page"] == pds["source_page"]
                and auxiliary["section_heading"] == pds["section_heading"]
            )
            compatible = _axes_compatible(auxiliary, pds)
            explicit = _explicit_pds_reference(
                document_text.get(auxiliary["document_id"], ""),
                pds,
            )
            conflict = len(best) > 1
            confidence, review_required = relationship_confidence(
                explicit_reference=explicit,
                same_section=same_section,
                same_provider=True,
                compatible_axes=compatible,
                conflict=conflict,
            )
            relationship_id = "rel:" + hashlib.sha256(
                f"{relationship_type}|{auxiliary['document_id']}|{pds['document_id']}".encode()
            ).hexdigest()[:20]
            evidence = ["same insurer"]
            if same_section:
                evidence.append("same source page and section")
            if compatible:
                evidence.append("compatible product axes")
            if explicit:
                evidence.append("explicit PDS date/title reference")
            if conflict:
                evidence.append("multiple equally strong PDS candidates")
            review_reason = (
                "Relationship requires human confirmation because evidence is not unique or explicit."
                if review_required
                else None
            )
            relationship = {
                "relationship_id": relationship_id,
                "relationship_type": relationship_type,
                "source_document_id": auxiliary["document_id"],
                "target_document_id": pds["document_id"],
                "confidence": confidence,
                "evidence": evidence,
                "review_required": review_required,
                "review_reason": review_reason,
            }
            relationships.append(relationship)
            if review_required:
                review_items.append(
                    {
                        "review_id": f"review:{relationship_id[4:]}",
                        "relationship_id": relationship_id,
                        "reason": review_reason,
                        "status": "pending",
                    }
                )
    return relationships, review_items


def _build_product_releases(
    documents: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    for pds in documents:
        if not (
            pds["document_type"] == "pds"
            and pds["version_status"] == "current"
            and pds["validation_status"] == "valid_pdf"
        ):
            continue
        linked = [
            relation
            for relation in relationships
            if relation["target_document_id"] == pds["document_id"]
        ]
        if any(relation["review_required"] for relation in linked):
            completeness = "review_required"
        elif linked:
            completeness = "associated"
        else:
            completeness = "pds_only"
        releases.append(
            {
                "release_id": (
                    f"{pds['insurer_code']}:{pds['effective_from'] or 'unknown'}:"
                    f"{pds['sha256'][:12]}"
                ),
                "insurer_code": pds["insurer_code"],
                "pds_document_id": pds["document_id"],
                "related_document_ids": sorted(
                    {relation["source_document_id"] for relation in linked}
                ),
                "geographic_scopes": pds["geographic_scopes"],
                "trip_frequencies": pds["trip_frequencies"],
                "plan_tiers": pds["plan_tiers"],
                "effective_from": pds["effective_from"],
                "completeness_status": completeness,
            }
        )
    return releases


def _undownloaded_document(
    provider: ProviderConfig,
    candidate: DiscoveredLink,
    document_type: str,
    *,
    error_code: str | None,
) -> dict[str, Any]:
    axes = _with_default_axes(
        classify_product_axes(candidate.context_text),
        provider.default_axes,
    )
    return {
        "document_id": _url_document_id(candidate.url),
        "insurer_code": provider.insurer_code,
        "brand_code": provider.brand_code,
        "issuer": provider.issuer,
        "underwriter": provider.underwriter,
        "document_type": document_type,
        "title": _document_title(candidate),
        "source_page": candidate.source_page,
        "discovered_url": candidate.url,
        "final_url": None,
        "section_heading": candidate.section_heading,
        "anchor_text": candidate.anchor_text,
        **axes,
        "effective_from": parse_effective_date(candidate.context_text),
        "version_status": candidate.version_status,
        "retrieval_status": "skipped",
        "validation_status": "not_validated",
        "parse_status": "not_attempted",
        "local_path": None,
        "sha256": None,
        "content_type": None,
        "size_bytes": None,
        "error_code": error_code,
        "evidence": list(candidate.evidence),
    }


def _failed_document(
    provider: ProviderConfig,
    candidate: DiscoveredLink,
    document_type: str,
    error: Exception,
) -> dict[str, Any]:
    document = _undownloaded_document(
        provider,
        candidate,
        document_type,
        error_code=getattr(error, "code", "document_download_failed"),
    )
    document["retrieval_status"] = "failed"
    document["validation_status"] = getattr(error, "validation_status", "not_validated")
    return document


def _parse_provider(payload: Any) -> ProviderConfig:
    if not isinstance(payload, dict):
        raise ValueError("Each provider config must be an object.")
    _require_keys(
        payload,
        {
            "insurer_code", "brand_code", "issuer", "underwriter",
            "allowed_domains", "start_pages", "seed_documents", "default_axes",
        },
        "provider",
    )
    insurer_code = _safe_code(payload["insurer_code"], "insurer_code")
    brand_code = _safe_code(payload["brand_code"], "brand_code")
    domains = _string_tuple(payload["allowed_domains"], "allowed_domains")
    if not domains:
        raise ValueError(f"Provider {insurer_code} requires allowed_domains.")
    pages_raw = payload["start_pages"]
    if not isinstance(pages_raw, list) or not pages_raw:
        raise ValueError(f"Provider {insurer_code} requires start_pages.")
    pages: list[StartPageConfig] = []
    for page in pages_raw:
        if not isinstance(page, dict):
            raise ValueError("Each start page must be an object.")
        _require_keys(
            page,
            {"url", "required_context_hints", "follow_link_hints"},
            "start_page",
        )
        pages.append(
            StartPageConfig(
                url=_nonempty_string(page["url"], "start_page.url"),
                required_context_hints=_string_tuple(
                    page["required_context_hints"], "required_context_hints"
                ),
                follow_link_hints=_string_tuple(
                    page["follow_link_hints"], "follow_link_hints"
                ),
            )
        )
    seed_documents_raw = payload["seed_documents"]
    if not isinstance(seed_documents_raw, list):
        raise ValueError("seed_documents must be an array.")
    seed_documents: list[SeedDocumentConfig] = []
    for document in seed_documents_raw:
        if not isinstance(document, dict):
            raise ValueError("Each seed document must be an object.")
        _require_keys(
            document,
            {"url", "source_page", "title", "section_heading", "version_status"},
            "seed_document",
        )
        version_status = _nonempty_string(
            document["version_status"], "seed_document.version_status"
        )
        if version_status not in {"current", "archived", "unknown"}:
            raise ValueError("seed_document.version_status is invalid.")
        seed_documents.append(
            SeedDocumentConfig(
                url=_nonempty_string(document["url"], "seed_document.url"),
                source_page=_nonempty_string(
                    document["source_page"], "seed_document.source_page"
                ),
                title=_nonempty_string(document["title"], "seed_document.title"),
                section_heading=_nullable_string(
                    document["section_heading"], "seed_document.section_heading"
                ),
                version_status=version_status,
            )
        )
    axes_raw = payload["default_axes"]
    if not isinstance(axes_raw, dict) or set(axes_raw) != set(_AXIS_VALUES):
        raise ValueError("default_axes must contain exactly the three product axes.")
    axes: dict[str, list[str]] = {}
    for axis, allowed in _AXIS_VALUES.items():
        values = list(_string_tuple(axes_raw[axis], axis))
        if not values or any(value not in allowed for value in values):
            raise ValueError(f"Provider {insurer_code} has invalid {axis} defaults.")
        axes[axis] = values
    return ProviderConfig(
        insurer_code=insurer_code,
        brand_code=brand_code,
        issuer=_nullable_string(payload["issuer"], "issuer"),
        underwriter=_nullable_string(payload["underwriter"], "underwriter"),
        allowed_domains=domains,
        start_pages=tuple(pages),
        seed_documents=tuple(seed_documents),
        default_axes=axes,
    )


def _select_providers(
    providers: tuple[ProviderConfig, ...],
    insurer_codes: Sequence[str],
) -> tuple[ProviderConfig, ...]:
    if not insurer_codes:
        return providers
    requested = set(insurer_codes)
    available = {provider.insurer_code for provider in providers}
    unknown = sorted(requested - available)
    if unknown:
        raise ValueError(f"Unknown insurer codes: {unknown}; available: {sorted(available)}")
    return tuple(provider for provider in providers if provider.insurer_code in requested)


def _relationship_score(auxiliary: dict[str, Any], pds: dict[str, Any]) -> int:
    score = 2 if _axes_compatible(auxiliary, pds) else 0
    if auxiliary["source_page"] == pds["source_page"]:
        score += 1
    if auxiliary["section_heading"] == pds["section_heading"]:
        score += 1
    return score


def _axes_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    for axis in _AXIS_VALUES:
        left_values = set(left[axis]) - {"unknown"}
        right_values = set(right[axis]) - {"unknown"}
        if left_values and right_values and left_values.isdisjoint(right_values):
            return False
    return True


def _explicit_pds_reference(text: str, pds: dict[str, Any]) -> bool:
    lowered = text.lower()
    effective_date = pds.get("effective_from")
    if effective_date and effective_date in lowered:
        return True
    title = str(pds.get("title") or "").lower()
    meaningful = re.sub(r"\b(?:travel|insurance|product|disclosure|statement|pds)\b", "", title)
    meaningful = re.sub(r"\W+", " ", meaningful).strip()
    return len(meaningful) >= 8 and meaningful in lowered


def _with_default_axes(
    inferred: dict[str, list[str]],
    defaults: dict[str, list[str]],
) -> dict[str, list[str]]:
    return {
        axis: list(defaults[axis]) if values == ["unknown"] else values
        for axis, values in inferred.items()
    }


def _choose_product_axes(
    source_axes: dict[str, list[str]],
    pdf_axes: dict[str, list[str]],
    defaults: dict[str, list[str]],
) -> dict[str, list[str]]:
    chosen: dict[str, list[str]] = {}
    for axis in _AXIS_VALUES:
        if source_axes[axis] != ["unknown"]:
            chosen[axis] = source_axes[axis]
        elif pdf_axes[axis] != ["unknown"]:
            chosen[axis] = pdf_axes[axis]
        else:
            chosen[axis] = list(defaults[axis])
    return chosen


def _header(headers: dict[str, str], name: str) -> str | None:
    return next(
        (value for key, value in headers.items() if key.lower() == name.lower()),
        None,
    )


def _header_int(headers: dict[str, str], name: str) -> int | None:
    value = _header(headers, name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _document_title(candidate: DiscoveredLink) -> str:
    if candidate.anchor_text.strip():
        return candidate.anchor_text.strip()[:300]
    filename = Path(urlsplit(candidate.url).path).stem.replace("_", " ").replace("-", " ")
    return filename.strip()[:300] or "Travel insurance document"


def _url_document_id(url: str) -> str:
    return "urlsha256:" + hashlib.sha256(url.encode("utf-8")).hexdigest()


def _run_error(
    insurer_code: str,
    stage: str,
    url: str | None,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "insurer_code": insurer_code,
        "stage": stage,
        "url": _without_query(url) if url else None,
        "code": code if _SAFE_CODE.fullmatch(code) else "acquisition_error",
        "message": (message or "Acquisition error")[:500],
    }


def _without_query(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def _require_keys(payload: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(payload))
    unexpected = sorted(set(payload) - expected)
    if missing or unexpected:
        raise ValueError(f"{label} keys invalid; missing={missing}, unexpected={unexpected}")


def _safe_code(value: Any, label: str) -> str:
    parsed = _nonempty_string(value, label)
    if not _SAFE_CODE.fullmatch(parsed):
        raise ValueError(f"{label} must be a lowercase safe identifier.")
    return parsed


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value.strip()


def _nullable_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, label)


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be an array of strings.")
    return tuple(item.strip() for item in value if item.strip())


def _positive_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{label} must be positive.")
    return float(value)


def _nonnegative_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{label} must be non-negative.")
    return float(value)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer.")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer.")
    return value
