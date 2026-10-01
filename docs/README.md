# Documentation index

Use this index to distinguish current operating instructions from design history.
Project documentation is in English. Source PDFs and their derived Markdown are
data, not project documentation; they retain their original content and language.

## Current reference

| Document | Purpose |
| --- | --- |
| [Root README](../README.md) | Project overview, installation, and entry points |
| [Project layout](project-layout.md) | Directory rules, new paths, and migration inventory |
| [Operator guide](user-guide.md) | Step-by-step CLI and UI workflows, recovery, and output locations |
| [API and file contracts](api.md) | Supported Python integration boundaries, CLI behavior, and artifact formats |
| [Architecture](architecture.md) | Runtime flows and module ownership |
| [Dependency policy](dependency-policy.md) | Approved dependencies and rules for additions |
| [Open issues](fix.md) | Confirmed unresolved defects and acceptance criteria |
| [Prompt guide](../prompts/README.md) | Existing model-prompt layout and editing rules |

For behavior that differs from a historical proposal, consult the current code,
contracts, operator guide, and open-issue list. Structural validity is not a
claim of extraction accuracy.

## Design decisions and historical material

- [ADR-001: PostgreSQL hybrid storage](decisions/001-postgresql-hybrid-storage.md)
  explains core relational tables and JSONB storage.
- [ADR-002: Approved Canonical Schema authority](decisions/002-approved-canonical-schema-authority.md)
  explains human approval and the extraction/storage gate.
- [ADR-003: Central model prompts](decisions/003-central-model-prompts.md)
  explains the existing prompt ownership and resolution rules.
- [ADR-004: Project layout](decisions/004-project-layout.md) explains the directory
  migration and preservation of historical provenance.
- [Approved Canonical Schema PRD](specs/approved-canonical-schema-prd.md) provides
  the requirements behind candidates, approval, and storage mappings.
- [Relational storage PRD](specs/relational-storage-prd.md) provides storage
  requirements; pending identity and idempotency changes are tracked in [fix.md](fix.md).
- [Extraction quality audit PRD](specs/extraction-quality-audit-prd.md) defines
  Travel quality-screening scope; current usage is in the
  [operator guide](user-guide.md#f-optional-llm-judge-and-human-review).
- [Travel consensus and Canonical review](specs/travel-consensus-canonical-review.md)
  records the five-proposal review workflow.
- [Travel document acquisition PRD](specs/travel-insurance-document-acquisition-prd.md)
  is an early August 2026 draft. Its original branch names, three-insurer scope,
  data model, and implementation suggestions are historical proposals. Current
  sources and acquisition behavior come from [configuration](../configs/travel_insurance/)
  and the operator guide.
- [September 2026 code review](reviews/main-review-2026-09-13.md) records historical
  findings and line references. Remaining R2/R3 risks are in [fix.md](fix.md);
  old line numbers do not identify current code locations.
- [Confirmed layout scope](ideas/project-layout.md) records the user's directory,
  retention, language, and compatibility choices.

Completed cross-vertical implementation plans have been removed from the working
tree. Their durable conclusions—one engine, manifest-owned vertical differences,
CLI-backed UI, and one prompt/validation boundary—are covered by the current
architecture and ADRs. Git history retains the implementation record.

Keep quick entry points in the root README, procedures in the operator guide,
interfaces in `api.md`, rationale in ADRs, and unresolved defects in `fix.md`.
Do not commit experimental outputs, credentials, or source PDFs as documentation.
