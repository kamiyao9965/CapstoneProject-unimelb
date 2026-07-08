"""Frequency voting over schema patches from multiple consensus runs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from src.refine.candidates.patch import EvidenceDocument, SchemaPatch


@dataclass
class FieldDecision:
    canonical_name: str
    target_group: str
    field_type: str
    description: str
    frequency: int
    total_runs: int
    decision: str
    aliases: list[str] = field(default_factory=list)
    source_runs: list[str] = field(default_factory=list)
    average_confidence: float = 0.0
    evidence_documents: list[EvidenceDocument] = field(default_factory=list)
    patch_types: list[str] = field(default_factory=list)
    rationale_samples: list[str] = field(default_factory=list)
    # Negative signal: runs where the model proposed reject_field for this
    # canonical name. Display-only - never reduces frequency or the decision.
    reject_votes: int = 0
    reject_rationale_samples: list[str] = field(default_factory=list)

    @property
    def frequency_label(self) -> str:
        return f"{self.frequency}/{self.total_runs}"

    @property
    def reject_votes_label(self) -> str:
        return f"{self.reject_votes}/{self.total_runs}"

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.canonical_name,
            "group": self.target_group,
            "type": self.field_type,
            "frequency": self.frequency_label,
            "decision": self.decision,
            "patch_types": self.patch_types,
            "aliases": self.aliases,
            "source_runs": self.source_runs,
            "average_confidence": round(self.average_confidence, 3),
            "description": self.description,
            "rationale_samples": self.rationale_samples,
            "reject_votes": self.reject_votes_label,
            "reject_rationale_samples": self.reject_rationale_samples,
            "evidence_documents": [
                document.to_dict() for document in self.evidence_documents
            ],
        }


def aggregate_patches(
    patches: list[SchemaPatch],
    total_runs: int,
    core_threshold: float = 0.8,
    conditional_threshold: float = 0.5,
    candidate_threshold: float = 0.2,
) -> list[FieldDecision]:
    grouped: dict[str, list[SchemaPatch]] = defaultdict(list)
    rejects: dict[str, list[SchemaPatch]] = defaultdict(list)
    for patch in patches:
        if patch.patch_type == "reject_field":
            rejects[patch.canonical_name].append(patch)
            continue
        grouped[patch.canonical_name].append(patch)

    # Fields that were only ever rejected produce no decision: there is no
    # proposal to review. Their reject votes attach to supported fields only.
    decisions = [
        _build_decision(
            canonical_name=name,
            patches=items,
            reject_patches=rejects.get(name, []),
            total_runs=total_runs,
            core_threshold=core_threshold,
            conditional_threshold=conditional_threshold,
            candidate_threshold=candidate_threshold,
        )
        for name, items in grouped.items()
    ]
    return sorted(decisions, key=lambda item: (-item.frequency, item.canonical_name))


def _build_decision(
    canonical_name: str,
    patches: list[SchemaPatch],
    reject_patches: list[SchemaPatch],
    total_runs: int,
    core_threshold: float,
    conditional_threshold: float,
    candidate_threshold: float,
) -> FieldDecision:
    source_runs = sorted({patch.source_run for patch in patches if patch.source_run})
    frequency = len(source_runs) if source_runs else len(patches)
    ratio = frequency / total_runs if total_runs else 0

    if ratio >= core_threshold:
        decision = "core"
    elif ratio >= conditional_threshold:
        decision = "conditional"
    elif ratio >= candidate_threshold:
        decision = "candidate"
    else:
        decision = "noise"

    confidences = [patch.confidence for patch in patches if patch.confidence > 0]
    average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    aliases = sorted(
        {
            patch.field_name
            for patch in patches
            if patch.field_name and patch.field_name != canonical_name
        }
    )

    reject_runs = {patch.source_run for patch in reject_patches if patch.source_run}
    reject_votes = len(reject_runs) if reject_runs else len(reject_patches)

    return FieldDecision(
        canonical_name=canonical_name,
        target_group=_most_common([patch.target_group for patch in patches]),
        field_type=_most_common([patch.field_type for patch in patches]) or "string",
        description=_first_non_empty([patch.description for patch in patches]),
        frequency=frequency,
        total_runs=total_runs,
        decision=decision,
        aliases=aliases,
        source_runs=source_runs,
        average_confidence=average_confidence,
        evidence_documents=_unique_evidence(patches),
        patch_types=sorted({patch.patch_type for patch in patches if patch.patch_type}),
        rationale_samples=_sample_rationales(patches),
        reject_votes=reject_votes,
        reject_rationale_samples=_sample_rationales(reject_patches),
    )


def _most_common(values: list[str]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        if value:
            counts[value] += 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _first_non_empty(values: list[str]) -> str:
    return next((value for value in values if value), "")


def _sample_rationales(patches: list[SchemaPatch], cap: int = 3) -> list[str]:
    samples: list[str] = []
    for patch in patches:
        rationale = patch.rationale.strip()
        if rationale and rationale not in samples:
            samples.append(rationale)
        if len(samples) >= cap:
            break
    return samples


def _unique_evidence(patches: list[SchemaPatch]) -> list[EvidenceDocument]:
    seen: set[tuple[str, str]] = set()
    evidence: list[EvidenceDocument] = []
    for patch in patches:
        for document in patch.evidence_documents:
            key = (document.path, document.quote_or_summary)
            if document.path and key not in seen:
                seen.add(key)
                evidence.append(document)
    return evidence
