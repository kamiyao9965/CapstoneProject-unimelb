# Post-extraction quality audit

**Status:** Implemented (Travel first; live judge run pending operator approval)

## Goal

After a Travel extraction has been produced, run an optional, offline quality
screen. A separate LLM judge sees the original PDF's parsed pages, the exact
approved Canonical Schema field definitions, and the extraction values. It
reports evidence-supported concerns and uncertainty. A human checks those
concerns and a reproducible small sample of judge-passed documents. This is
quality feedback, not a second extraction or a storage gate.

## Scope and decisions

- Keep existing JSON/schema validation and PostgreSQL loading unchanged. Being
  loadable says nothing about factual correctness.
- One judge request per extraction artifact; no five-vote ensemble for this
  final screen. The five-run consensus belongs to schema discovery/refinement.
- Compare against PDF pages, not only the extraction's self-reported evidence.
  Refuse over-limit inputs instead of silently truncating context.
- Return `pass`, `review`, or `uncertain`, qualitative correctness, evidence
  support and uncertainty ratings, and bounded, field-specific findings with
  page/quote when available. Unsupported quotations are visibly marked, never
  silently accepted as verified citations.
- Queue every finding or uncertain verdict, plus a seeded sample of passes.
  Human decisions are `issue_found`, `no_issue`, or `uncertain` with notes.
- Persist immutable per-document judge reports, a bound review queue, and
  queue-bound review decisions under `outputs/`. Do not change extraction JSON,
  Canonical Schema, or database records from either step.
- Do not treat repeated umbrella product names across plan tiers as proof of a
  quality defect. Existing storage identity behavior is a separate open issue.
- Report judge alert rate, human confirmation/dismissal and sampled-pass misses.
  Without representative labels, do not label these numbers "accuracy".

## Interfaces

The `quality-audit` CLI command takes a Travel manifest, an extraction artifact
folder, optional approved Canonical Schema/source root, a new output directory,
provider/model, parser, sample rate and seed. The operator console wraps this
same command. `src/quality_review_app.py` opens the resulting review queue and
records checker decisions; it never calls an LLM or writes PostgreSQL.

All model calls use the existing provider and structured-output boundary, with
at most two schema-repair attempts. Each attempt goes to a separate quality
usage log. Model/PDF calls are only made on explicit command execution, never
during module import, form rendering, or tests.

## Acceptance

Offline fake-provider tests cover valid reports, malformed judge output and
bounded repairs, source/schema mismatch before calling the judge, citation
verification, deterministic pass sampling, queue/decision binding and the
review UI. No raw insurer PDF, live model, or PostgreSQL write is needed for
development verification.
