# Model prompt catalogue

All editable text sent to a model by the Health, Pet, Travel and Car pipelines lives under
this directory. The per-vertical files are the main **system prompts**; the
`shared/` files are request, feedback, repair, and provider-format templates.

Each of `private_health/`, `pet_insurance/`, `travel_insurance/` and
`car_insurance/` supplies `discovery.md`, `patch.md`, `extraction.md` and
`quality_audit.md`. Shared templates are `discovery_request.md`, `patch_request.md`,
`extraction_request.md` and `quality_audit_request.md` in `shared/`.
`schema_generation.md` explains the strict node descriptor: dynamic field names
are values compiled locally to closed `item_schema`, not an operator-maintained
field list. Native-schema/domain validation shares the bounded repair cycle.

Car's `review_v5.md` / `diagnostics_v5.md` reference guidance is appended only for
schemas declaring the complete compatible layout. Checklist completeness and
text-inference instructions apply only to compatible single-PDF extraction;
explicit amendment groups use references only and defer effective terms to judge.
Earlier v4 reference files remain available for historical explanation.

The remaining `shared/` files support refinement and common call behavior:

- `refinement_feedback.md` attaches the previous round's feedback to discovery.
- `feedback_*.md` produce holdout-analysis instructions for later rounds.
- `structured_repair.md` asks for a complete corrected JSON object after a
  failed local validation, at most twice per logical request.
- `deepseek_json_schema.md` supplies DeepSeek's JSON-object schema instruction.

The files are plain text with Python `{name}` substitution placeholders. Keep
the placeholder names when editing; write literal braces as `{{` and `}}`.
PDF pages, schema JSON, extraction values, validation errors, and document paths
are inserted at runtime and are not stored here. Contracts and business
validation remain in `contracts/` and `src/`, not in prompts.

`configs/<vertical>/manifest.json` selects each vertical's main prompt.
Built-in manifests point at `../../prompts/<vertical>/`. Custom manifests may
still use prompt files inside their own configuration package. The loader
rejects missing, empty, cross-vertical, and other out-of-scope prompt paths.

Changing a prompt can change model output even if the JSON contract stays the
same. Run a new experiment/output directory after editing a prompt, especially
for quality audits: `--resume` is for the **same** prompt version. New
judge reports record both the main system-prompt hash and a bundle hash covering
the shared request, repair and DeepSeek format templates; resume rejects a
changed bundle. Older reports lacking that bundle hash cannot be reused after shared templates
change; regenerate reports under the current workflow. Use offline tests
before any approved, paid model run.
