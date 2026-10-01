# Project layout

## Problem Statement

How can project collaborators find source code, configuration, documentation,
source PDFs, parsed Markdown, and experiment results without knowing historical
directory conventions?

## Recommended Direction

Use a mixed layout: source code grouped by responsibility under `src/`, vertical
configuration under `configs/`, model text under the existing `prompts/`, and
English project documentation under `docs/`. Keep a short root README and the
agent entry files where their tools discover them. Preserve the existing engine
ownership boundaries while grouping Streamlit presentation code under `src/ui/`
and naming the PDF package `src/pdf_ingestion/`.

Group data by format: `data/pdf/<vertical>/` and
`data/markdown/<vertical>/<parser>/`. Preserve insurer/category subdirectories;
keep historical Markdown of unknown provenance under `legacy/`. Put parser
caches under `.cache/pdf/<vertical>/`. Group results by vertical under
`outputs/<vertical>/`, then by purpose: schemas, extractions, quality, logs,
experiments, acquisition, evaluation, errors, exports, and archive as needed.

## Alternatives Considered

- Documentation only: low effort, but existing and future outputs remain mixed.
- Everything by format: easy to find PDFs, but separates related run artifacts.
- Everything by vertical: preserves business context, but hides data formats.
- Everything by run: good experiment isolation, but duplicates shared inputs.
- Mixed layout: the selected balance for finding inputs and reviewing results.

## Key Assumptions to Validate

- [x] New commands and imports can use the new paths exclusively; verify CLI
  help, module imports, Streamlit entry points, and the offline suite.
- [x] Existing data and output files can be relocated without changing bytes;
  record old/new paths and SHA-256 checksums and verify every move.
- [x] Parser routes remain distinguishable; preserve separate MinerU,
  PDFingestor, and legacy Markdown directories.
- [x] Project documentation can be translated without changing its technical
  meaning; retain historical status labels and check links and code examples.

## MVP Scope

Move source files, configuration references, documentation, existing PDFs,
Markdown, caches, and outputs into the agreed layout. Update default writers,
readers, imports, tests, and command examples so new runs follow the same rules.
Save a migration inventory and verify local data preservation and offline
behavior. The user confirmed this scope and saving this note on 2026-10-01.

## Not Doing (and Why)

- Deleting historical runs or logs: the user requires all of them to survive.
- Rewriting historical artifact contents: paths and hashes are part of their
  provenance. The migration inventory records new locations separately.
- Resuming relocated historical runs: their identity bindings require a
  separate review; start new runs with the new paths.
- Changing extraction algorithms, prompts, or approved Canonical Schema
  contents: this is a layout migration, not a model or schema change.
- Running providers, downloads, or database writes: offline verification is
  sufficient for this migration.
- Backward-compatible filesystem aliases: the user chose new paths only.

## Open Questions

None required before implementation. Unidentified historical files will be
preserved with their original names rather than guessed or deleted.

## Verification

Completed on 2026-10-01. The offline suite ran 461 tests successfully, with the
live PostgreSQL test skipped. Python compilation, CLI help for both main entry
points, and all four Streamlit entry points (through offline AppTest tests)
passed. All local documentation links resolved, including heading anchors;
the project-documentation scan found no remaining Chinese text.

All 1,580 inventoried files retained their sizes and SHA-256 checksums, including
1,514 relocated files. Prompt text, reference schemas, and dependencies match
their pre-migration copies. No live model-provider, MinerU, download, or database
operation was used for this verification. See [migration details](../project-layout.md).
