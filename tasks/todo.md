# Relational Storage Tasks

- [ ] Define and validate the Travel storage mapping.
  - Acceptance: mapping has a version, vertical, collection path, identity
    fields, and deterministic taxonomy mappings.
  - Verify: focused mapping-contract tests.

- [ ] Add storage capability to vertical manifests.
  - Acceptance: Travel enables it; private health remains disabled.
  - Verify: manifest tests.

- [ ] Implement canonical database metadata.
  - Acceptance: empty database can create all core and Travel tables.
  - Verify: SQLite creation and PostgreSQL DDL compilation tests.

- [ ] Implement fail-closed Travel mapping.
  - Acceptance: valid artifacts produce deterministic records; malformed input
    fails before database access.
  - Verify: mapper unit tests.

- [ ] Implement transactional idempotent persistence.
  - Acceptance: repeated loads do not duplicate rows; a failed load rolls back.
  - Verify: SQLite integration tests.

- [ ] Add storage CLI commands and documentation.
  - Acceptance: commands resolve credentials from an environment-variable name
    without printing its value.
  - Verify: CLI tests and `python src/run.py --help`.

- [ ] Run final verification and review.
  - Verify: compileall, full unittest suite, diff review, and clean Git status.
