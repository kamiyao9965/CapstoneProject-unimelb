# Approved Canonical Schema Tasks

- [x] Define the Canonical Schema contract and lifecycle validator.
  - Acceptance: approved schemas require a valid human review record; candidate
    schemas fail closed at compiler boundaries.
  - Verify: focused contract and semantic validation tests.
  - Files: `contracts/`, `src/schema/`, `tests/test_canonical_schema.py`.

- [x] Compile extraction contracts from approved Canonical Schemas.
  - Acceptance: Travel multiple-product output has closed field types,
    requiredness, enums, and document notes.
  - Verify: validate valid and invalid payload fixtures offline.
  - Files: `src/schema/canonical.py`, `tests/test_canonical_schema.py`.

- [x] Compile vertical SQLAlchemy metadata.
  - Acceptance: approved fields produce typed extension columns, enum checks,
    a release foreign key, and JSONB attributes.
  - Verify: SQLite creation and PostgreSQL DDL compilation.
  - Files: `src/storage/schema.py`, `src/storage/canonical.py`,
    `tests/test_canonical_storage.py`.

- [x] Compile deterministic load plans.
  - Acceptance: validated products produce stable core bindings and extension
    records; JSONB fields are preserved without LLM inference.
  - Verify: pure mapper tests, including invalid and repeated input.
  - Files: `src/storage/canonical.py`, `tests/test_canonical_storage.py`.

- [x] Add reviewed-schema compiler CLI and documentation.
  - Acceptance: approved schema writes generated extraction contract and SQL
    preview without overwriting; candidates fail with a clear message.
  - Verify: CLI tests and `src/run.py --help`.
  - Files: `src/run.py`, `README.md`, CLI tests.

- [x] Run final verification and review.
  - Verify: compileall, full unittest suite, diff review, secret scan, and clean
    Git status after atomic commits.
