# Model prompt catalogue

All editable text sent to a model by the Health and Travel pipelines lives under
this directory. The per-vertical files are the main **system prompts**; the
`shared/` files are request, feedback, repair, and provider-format templates.

| Stage | Private Health | Travel insurance | Shared request template |
| --- | --- | --- | --- |
| Discover a schema | `private_health/discovery.md` | `travel_insurance/discovery.md` | `shared/discovery_request.md` |
| Propose schema patches | `private_health/patch.md` | `travel_insurance/patch.md` | `shared/patch_request.md` |
| Extract records | `private_health/extraction.md` | `travel_insurance/extraction.md` | `shared/extraction_request.md` |
| Judge an extraction | — | `travel_insurance/quality_audit.md` | `shared/quality_audit_request.md` |

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
for Travel quality audits: `--resume` is for the **same** prompt version. New
judge reports record both the main system-prompt hash and a bundle hash covering
the shared request, repair and DeepSeek format templates; resume rejects a
changed bundle. Older reports lack that optional bundle hash and may be reused
only while the shared templates retain their original text. Use offline tests
before any approved, paid model run.
