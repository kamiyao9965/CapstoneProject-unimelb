# Dependency Policy

Dependencies require user approval, one owning module, and a documented reason
that the standard library or an existing dependency is insufficient.

## Existing approved dependencies

The existing pipeline dependencies remain listed in `requirements.txt` and are
owned by their current model-provider, PDF ingestion, validation, review,
normalisation, and evaluation modules.

## Relational storage dependencies

- `SQLAlchemy>=2.0,<3`
  - Owner: `src/storage/`.
  - Reason: defines one parameterised PostgreSQL schema, bound statements, and
    transaction boundary. Offline tests validate metadata and PostgreSQL SQL
    compilation without substituting another database dialect.
  - Approval: user approved database dependencies on 2026-08-18.

- `psycopg[binary]>=3.2,<4`
  - Owner: PostgreSQL connection through `src/storage/`.
  - Reason: SQLAlchemy requires a DBAPI driver to connect to PostgreSQL. The
    binary extra supplies the client libraries for supported local platforms
    and avoids a system `libpq` build prerequisite. Storage commands reject
    non-PostgreSQL URLs; SQLite is not a supported storage backend.
  - Approval: user approved database dependencies on 2026-08-18.

## PDF parsing dependencies

- `mineru[all]==3.4.3`
  - Owner: `src/PDFingestor/mineru.py`, reached only through
    `--document-parser mineru` (CLI) or the UI "PDF parsing route" option.
  - Reason: provides a second, layout-model-based PDF parsing route that can be
    compared with the default pdfplumber-based PDFingestor route. MinerU runs
    locally with the `pipeline` backend in a separate Python process that calls
    MinerU's `do_parse()`; its content list is converted into the same
    `ParsedPDF` structure, so prompts, validation and storage are unchanged. The
    default route does not import or run MinerU.
  - Runtime: MinerU needs its pipeline models locally (`mineru-models-download`
    or an existing `~/mineru.json` models directory). The worker starts no HTTP
    service and does not upload documents. The `mineru` CLI is not used because
    its temporary local API service failed status polling during CPU-heavy
    post-processing in the 2026-09-15 smoke test.
  - Approval: first approved on 2026-07-11, removed on 2026-09-13 with the
    unused `common/document_preprocessor.py` entry point, and re-approved by the
    user on 2026-09-15 for the dual parsing route.

## Rules

- Keep provider SDKs out of storage modules and database clients out of model
  request modules.
- Do not add a second ORM, migration framework, or document database without
  explicit approval.
- Update `requirements.txt` and this file together.
- Do not commit database URLs, passwords, dumps, or generated local databases.
