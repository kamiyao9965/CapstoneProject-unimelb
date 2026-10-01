# PRD: Official Travel Insurance document acquisition and association

> Historical draft, August 2026. Branch names, the three-insurer scope, proposed
> commands, directory trees, and implementation phases below record the original
> proposal, not current operating instructions. Consult the
> [documentation index](../README.md), [operator guide](../user-guide.md),
> [current layout](../project-layout.md), and current manifests. Translating this
> draft does not approve or implement its outstanding requirements.

## Document information

| Item | Value |
| --- | --- |
| Status | Draft, awaiting business and technical review |
| Version | 0.1.0 |
| Date | 2026-08-10 |
| Vertical | Australian Travel Insurance |
| Proposed implementation branch | `feat/travel-insurance` |
| Proposed baseline | `origin/merged-pipeline` |
| Scope | Product requirements, acquisition boundaries, document association, acceptance criteria, and implementation recommendations |

## 1. Executive summary

This proposal adds Travel Insurance to the existing Australian Private Health
Insurance schema-discovery project. The first phase does not address quotations,
recommendations, or policy sales. It establishes an auditable, repeatable process
for discovering and downloading PDS, SPDS, Policy Wording, Benefits Summary, and
Brochure documents from public official Australian insurer websites, then links
documents belonging to the same version into a `product_release` bundle.

The objective is a trustworthy data collection rather than maximum PDF volume.
Each file should identify where it was found, when it was downloaded, whether it
is current, which products it covers, which PDS it relates to, and whether its
content changed. These facts support later schema discovery, extraction,
comparison, and enterprise data services.

The proposed first release covers Allianz, Cover-More, and Southern Cross Travel
Insurance, with `international_single_trip`, `annual_multi_trip`, and `domestic`
products. Acquisition is configuration-driven and static-page-first, following
at most one document-center page level rather than building a general web crawler.

## 2. Background and problem

### 2.1 Existing project

The baseline already supports PDF processing, schema discovery, structured output,
validation, refinement, human review, holdout evaluation, and cost estimation.
Its contracts, sampling categories, and business checks primarily target Private
Health Insurance.

Travel uses similar PDF inputs and can reuse shared capabilities. Its product
taxonomy, benefits, version relationships, and website publishing patterns differ,
so changing only prompts or input directories is insufficient for a reliable migration.

### 2.2 User problem

Enterprise users need to compare Travel products, but information is spread across
product pages, document centers, and PDFs:

- PDS/Policy Wording is the primary contract, with inconsistent link locations/names.
- SPDS modifies an existing PDS; retaining only the base can produce outdated results.
- Brochure/Benefits Summary aids comparison but cannot replace the PDS.
- One document may cover Single Trip, Annual Multi-Trip, and Domestic products.
- Current and archived documents often coexist; filenames alone do not identify versions.
- The same PDF can appear at several URLs, or move to a new URL without changing content.

Without source, version, and relationship handling, a model may correctly read a
PDF yet produce data for the wrong product version: technical success with a
business error.

### 2.3 Why Travel first

Travel has high reuse potential: public PDF inputs and benefits that fit structured
contracts. Unlike Car Insurance quotations that often need personal information,
this first phase can provide useful data without entering quote flows or collecting
personal details.

The proposal supports enterprise users and low-cost vertical extension by first
building reusable official-document/version data, then potentially adding product
comparison, coverage analysis, and recommendations.

## 3. Product goals

### 3.1 Core goals

1. Discover current Travel documents from configured official public pages.
   Official sources strengthen provenance and reduce third-party/outdated-copy risk.
2. Validate, download, and deduplicate PDFs by content hash. URLs, names, and page
   structures change; content hashes provide stable document identity.
3. Link PDS, SPDS, and Brochure/Benefits Summary to a product release. One file may
   not describe all current terms, particularly when supplements amend the base.
4. Record complete provenance, status, and errors per run. Enterprise data must
   distinguish no documents from acquisition failure and support reproduction.
5. Feed the existing discovery/extraction pipeline rather than create an isolated
   second processing system.

### 3.2 Success definition

The proposed MVP should:

- Configure at least three insurers independently, without one failure stopping others.
- Discover at least 95% of a manually labelled current PDS/SPDS/Brochure set.
- Validate every saved file's PDF type, signature, and size.
- Achieve at least 95% precision for high-confidence associations on manually
  confirmed document relationships.
- Avoid saving identical content again and report added, changed, unavailable,
  and unchanged documents on repeated runs.
- Trace every file to source page, discovered/final URL, retrieval time, and hash.
- Send ambiguous versions/relationships to review rather than silently mark them current.

The coverage denominator is the labelled target set, not every PDF on a website;
claim forms, FSG, privacy policies, and historical documents are not all core targets.

## 4. Non-goals

The MVP excludes:

- Online quotation, purchase, login, and customer-portal flows.
- Submission of names, ages, destinations, travel dates, or health information.
- Personalized premiums and price-comparison promises.
- Bypassing CAPTCHAs, access controls, robots rules, or technical restrictions.
- General web search or unlimited recursive crawling.
- Automatic promotion of low-confidence associations to authoritative data.
- Treating marketing brochures as final contractual authority.
- Public redistribution of original PDFs; commercial use/redistribution requires
  a separate legal decision.
- LLM calls inside the crawler. Models belong downstream and must not influence
  deterministic acquisition outcomes.

These boundaries limit privacy, legal, operating-cost, and correctness risks and
keep the MVP focused on the official-document data layer.

## 5. Assumptions to confirm

If review invalidates an assumption, update the PRD before implementation:

1. Initial geography is Australia, using official Australian product pages.
2. Initial use is internal research/product validation without republishing full PDFs.
3. Current saleable products are primary; historical versions can be recorded but
   are not default schema-discovery inputs.
4. The team accepts limited human review to improve version/relationship reliability.
5. PDS/Policy Wording is primary; SPDS amends it; brochures and summaries are supporting material.
6. `origin/merged-pipeline` remains the team's approved integrated baseline at implementation.
7. New dependencies require dependency-policy review and user approval.

## 6. Users and scenarios

### 6.1 Data engineers and researchers

Run one acquisition command to obtain validated PDFs, manifests, and relationships
without manually visiting each insurer.

### 6.2 Schema researchers

Select current, related, clearly sourced documents for Travel field discovery,
avoiding obsolete versions and duplicate-content samples.

### 6.3 Product and business analysts

Identify each insurer's product types, applicable PDS/SPDS, and the document
supporting each benefit statement.

### 6.4 Reviewers

Inspect low-confidence version/association proposals using source pages, titles,
effective dates, and text evidence, then accept, edit, or reject them.

## 7. MVP scope

### 7.1 Initial insurers

| Provider | Proposed official entry | Reason |
| --- | --- | --- |
| Allianz | [Travel Insurance](https://www.allianz.com.au/travel-insurance.html) | Broad product range; tests a PDS covering multiple plans |
| Cover-More | [PDS documents](https://www.covermore.com.au/pds) | Explicit document entry; tests current/previous discovery |
| Southern Cross Travel Insurance | [Comprehensive policies](https://scti.com.au/our-policies/comprehensive) | Clear product/policy-wording pages; tests multiple products and sources |

Southern Cross Annual Multi-Trip and Domestic pages provide additional entries
for one insurer. nib and 1Cover could follow after official domains, document
entries, and terms are confirmed.

### 7.2 Product types

Proposed canonical types:

- `international_single_trip`
- `annual_multi_trip`
- `domestic`

Defer `inbound`, `medical_only`, `working_overseas`, and `cruise_only` until the
shared association mechanism is validated. Early taxonomy expansion increases
ambiguity and labelling cost. Rental vehicle excess remains a Travel benefit,
not a separate vertical or product type.

### 7.3 Document types

| Canonical type | Business role | Proposed MVP handling |
| --- | --- | --- |
| `pds` | Primary contract, including primary Policy Wording | Required acquisition |
| `spds` | Supplement/amendment to identified PDS | Required acquisition and `amends` relationship |
| `benefit_summary` | Benefit limits and plan differences | Acquire and associate when present |
| `brochure` | Marketing/overview | Acquire and associate when present |
| `tmd` | Target Market Determination | Optional; outside core comparison |
| `fsg` | Financial Services Guide | Record discovery; do not download by default |
| `claim_form` | Claims form | Skip |
| `archive` | Historical version | Record; exclude from current bundles by default |

Benefits Summary is separate from Brochure because structured benefit tables and
marketing overviews have different comparison/discovery value. Neither replaces
the legal role of the PDS.

## 8. Concepts and association model

### 8.1 Why three file lists are insufficient

Relationships are not one-to-one: a PDS can cover several types; an SPDS can amend
several PDS files; a summary can compare several plans; and a new PDS can supersede
an old one without automatically inheriting its brochure. Support many-to-many
relationships through `product_release`, representing an insurer's applicable
document set for a period.

### 8.2 Entity model

```text
Provider
  └── Product Family
        └── Product Release
              ├── PDS / Policy Wording
              ├── SPDS
              ├── Benefits Summary
              └── Brochure

Document ── Document Relationship ── Document
    │
    └── Document Release Link ── Product Release
```

### 8.3 Relationship types

| Source | Target | `relationship_type` | Meaning |
| --- | --- | --- | --- |
| SPDS | PDS | `amends` | Supplements/amends the target PDS |
| Brochure | PDS | `summarizes` | Summarizes the associated product |
| Benefits Summary | PDS | `summarizes_benefits_of` | Explains benefits and limits |
| New PDS | Old PDS | `supersedes` | Replaces the earlier PDS |
| TMD | Product Release | `targets_market_for` | Describes the target market |

### 8.4 Product release identity

The proposed `product_release_id` combines insurer, normalized family, and base
PDS version, for example:

```text
allianz:travel_insurance:2026-01-01:ab12cd34
```

Use an explicit effective date and a short primary-PDS content hash. The hash
distinguishes content changes republished with the same effective date.

### 8.5 Relationship evidence

Use evidence in descending strength:

1. PDF text explicitly names the amended/supplemented/applicable PDS and date.
2. The official page groups files in the same current-product/document section.
3. Insurer, product name, and applicable product types agree.
4. Effective periods overlap without a newer superseding PDS.
5. URLs, filenames, and anchor text provide supporting clues.

Explicit text references outrank names/URLs because publishers may change paths
or use ambiguous filenames.

### 8.6 Confidence and review

| Confidence | Handling |
| --- | --- |
| `high` | Associate automatically; preserve evidence and rules |
| `medium` | Review before joining a current bundle |
| `low` | Leave unassociated; exclude from product-level downstream processing |

Every automatic association must include evidence. A confidence number alone does
not explain the relationship to reviewers.

## 9. User workflow

### 9.1 Configure sources

Maintain insurer codes, entry URLs, allowed domains, and product/document hints
in a tracked source registry.

### 9.2 Dry run

Discover pages and candidates without downloading PDFs. Inspect candidate counts,
domains, current/archive judgments, and exclusion reasons.

### 9.3 Acquire documents

Process insurers independently, validate robots and URLs, follow at most one
eligible document-page level, then validate/download PDFs.

### 9.4 Resolve metadata and relationships

Inspect page context and extractable PDF text for document/product types,
effective dates, version states, and relationships.

### 9.5 Human review

Review `needs_review` items. Accepted items join current bundles. Rejections retain
decisions/reasons so the next run does not blindly propose the same association.

### 9.6 Downstream selection

Schema discovery selects only current, validated PDFs whose relationships are
confirmed or high confidence and whose content does not duplicate selected samples.

## 10. Functional requirements

### FR-01: Configurable source registry (P0)

Add/edit insurers through configuration without copying a crawler per company.
Website changes should mostly affect URLs, selection rules, and hints, while shared
security/download logic stays consistent. Proposed fields:

```json
{
  "provider_code": "allianz",
  "display_name": "Allianz",
  "entry_urls": ["https://www.allianz.com.au/travel-insurance.html"],
  "allowed_domains": ["allianz.com.au", "www.allianz.com.au"],
  "max_follow_depth": 1,
  "max_pages": 20,
  "requests_per_second": 0.5,
  "follow_page_hints": ["policy information", "policy wording", "pds"],
  "include_document_hints": ["pds", "product disclosure", "supplementary", "benefits summary"],
  "exclude_document_hints": ["claim form", "privacy", "financial services guide"],
  "product_type_hints": {
    "annual multi-trip": "annual_multi_trip",
    "domestic": "domestic",
    "comprehensive": "international_single_trip"
  }
}
```

### FR-02: Bounded page discovery (P0)

Prefer static HTML. Extract links from entry pages and follow only one eligible
document-page level on official allowlisted domains. No unbounded recursion.
The usual product-page → policy-documents → PDF pattern needs limited depth;
unlimited crawling adds quote, claims, news, and privacy noise/risk.

### FR-03: Preserve page context (P0)

Store anchor text, nearest section heading, source page, and date text per
candidate. Current/previous status often lives around the link rather than in
the filename; storing only URLs loses valuable version evidence.

### FR-04: URL and access safety (P0)

Require HTTPS; validate allowlists before requests and after redirects; reject
loopback, private, link-local, and local-file targets; respect robots.txt and use
a clear User-Agent. Set connect/read/total timeouts and per-insurer rate, page,
and candidate limits. Page-supplied URLs/redirects create SSRF and cross-domain
risk; hard limits also contain erroneous configurations.

### FR-05: Candidate classification (P0)

Distinguish `pds`, `spds`, `benefit_summary`, `brochure`, `tmd`, `fsg`, `claim_form`,
and `unknown`, saving matched rules/evidence rather than labels alone. Document
roles differ; treating every PDF as a brochure can misrepresent marketing summaries
as complete terms downstream.

### FR-06: PDF validation and streaming downloads (P0)

Stream downloads with a default 50 MiB cap. Check both Content-Type and `%PDF-`
signature; reject HTML errors, empty files, and oversized responses. Move temporary
files into final paths only after validation. Compute SHA-256 for identity.
A `.pdf` URL may return login/error HTML; temporary validated writes prevent
partial files from contaminating the dataset.

### FR-07: Idempotency, deduplication, and content changes (P0)

Save each SHA-256 content once while retaining multiple source URLs. A changed
hash at the same URL becomes a new version or `content_changed` event, never an
overwrite. URL-only deduplication misses both duplicate links and replaced content.

### FR-08: Version status (P0)

Support `current`, `archived`, `superseded`, `unknown`, and `needs_review`.
Presence on an entry page alone does not establish currentness. Combine section,
effective-date, supersession, and text evidence. A stale PDS incorrectly marked
current can be more harmful than a missed document; uncertainty must fail closed.

### FR-09: Relationships and bundles (P0)

Create explicit PDS/SPDS/Benefits Summary/Brochure relationships and product-release
bundles. One document can link to several releases. Multi-plan PDS files make
single-product-only directories prone to duplicated files or lost applicability.

### FR-10: Manifest and provenance (P0)

Record every candidate/download outcome: success, duplicate, skip, and failure.
Example:

```json
{
  "document_id": "sha256:...",
  "provider_code": "allianz",
  "document_type": "spds",
  "product_types": ["international_single_trip", "annual_multi_trip"],
  "title": "Supplementary Product Disclosure Statement",
  "source_page": "https://...",
  "discovered_url": "https://...",
  "final_url": "https://...",
  "section_heading": "Current policy documents",
  "effective_from": "2026-05-10",
  "effective_to": null,
  "version_status": "current",
  "sha256": "...",
  "content_type": "application/pdf",
  "size_bytes": 1234567,
  "downloaded_at": "2026-08-10T12:00:00Z",
  "retrieval_status": "downloaded",
  "crawler_version": "0.1.0"
}
```

Success-only records hide coverage gaps. Skips and failures are also data-quality evidence.

### FR-11: Structured error codes (P0)

At minimum:

```text
ROBOTS_DISALLOWED
OFF_DOMAIN_URL
OFF_DOMAIN_REDIRECT
PRIVATE_NETWORK_TARGET
HTTP_ERROR
TIMEOUT
SIZE_LIMIT_EXCEEDED
NOT_PDF
DUPLICATE_CONTENT
NO_DOCUMENT_CANDIDATES
AMBIGUOUS_DOCUMENT_TYPE
AMBIGUOUS_VERSION
AMBIGUOUS_RELATIONSHIP
```

Stable codes support aggregation, alerts, and tests. Original exception text may
be retained as redacted detail, not the sole interface.

### FR-12: Insurer failure isolation (P0)

One insurer or URL failure must not stop the entire acquisition. Final status may
be `success`, `partial_success`, or `failed`. Temporary outages and site redesigns
are expected; preserve useful results while exposing failure scope.

### FR-13: Dry run and report (P0)

Show pages to visit, candidate PDFs, classifications, version decisions,
exclusions, and warnings without writing raw PDFs. This provides a cheap check
before enabling new source configuration and helps business reviewers assess rules.

### FR-14: Optional dynamic-page adapter (P1)

Enable browser rendering for a particular insurer only when static HTML and official
document entries are insufficient. Browser runtimes such as Playwright are larger,
slower, and sensitive to page changes. They require explicit approval and per-insurer
activation, not a default path.

### FR-15: Historical comparison (P1)

Compare adjacent runs for `added`, `removed`, `content_changed`, `metadata_changed`,
and `unchanged`. Ongoing PDS updates and SPDS publication are valuable beyond a
one-time download.

## 11. Nonfunctional requirements

### 11.1 Correctness

Do not guess uncertain types, versions, or relationships. Preserve evidence for
automatic classification/association. Downstream consumers accept only successful,
contract-validated artifacts.

### 11.2 Reproducibility

The same configuration, page snapshots, and file content produce the same
canonical classifications/relationships. Nondeterministic run IDs/times must not
affect semantic comparisons.

### 11.3 Performance and resource limits

Defaults: at most 0.5 requests/second per insurer, one followed page level,
20 HTML pages, 100 PDF candidates, and 50 MiB per PDF. Any concurrency is bounded
per insurer/domain; serial MVP execution is acceptable. Protect websites and
operational control, then tune using real measurements rather than premature concurrency.

### 11.4 Maintainability

Express insurer differences in configuration or narrow adapters. Do not duplicate
URL, security, download, hashing, manifest, or error logic. Keep acquisition separate
from schema discovery, provider calls, and PDF content extraction.

### 11.5 Auditability

Retain original/final URLs, timestamps, hashes, classification rules, relationship
evidence, and review decisions. New acquisition runs must not silently overwrite
human decisions.

## 12. Proposed system flow

```text
Tracked source registry
  -> URL/robots/security validation
  -> static entry-page discovery
  -> bounded document-page follow
  -> candidate classification with page context
  -> streaming PDF validation/download
  -> SHA-256 identity and de-duplication
  -> PDF metadata/text inspection
  -> version resolution
  -> relationship resolution
  -> review queue for ambiguity
  -> current product-release bundles
  -> schema discovery / extraction pipeline
```

Run acquisition separately from model processing. Network failure, file validity,
version resolution, and model-output errors need independent retry, testing, and audit.

## 13. Proposed data and file structure

Historical proposal; current paths are in [project layout](../project-layout.md):

```text
configs/travel_insurance/
  sources.json

contracts/travel_insurance/
  acquisition_run.schema.json
  document_manifest.schema.json
  product_release.schema.json
  relationship_review.schema.json

data/travel_insurance/raw/PDFs/
  allianz/
    pds/
    spds/
    benefit_summary/
    brochure/
  cover_more/
  southern_cross/

outputs/travel_insurance/acquisition/
  <run_id>/
    run.json
    documents.jsonl
    product_releases.json
    review_queue.json
    change_report.json
    errors/
```

Raw PDFs/runtime outputs remain ignored by Git. Source configuration and JSON
contracts are tracked because they define repeatable behavior and public contracts.
Proposed file naming:

```text
<provider>/<document_type>/<sha256-prefix>_<sanitized-title>.pdf
```

Do not use product type as the sole directory hierarchy: a file can cover several
types. Store applicability in manifest arrays.

## 14. Proposed CLI

These are historical interface sketches, not current executable instructions.
Current acquisition uses `src/run.py crawl` as described in the operator guide.

```bash
.venv/bin/python -m src.acquisition.travel \
  --config configs/travel_insurance/sources.json \
  --dry-run
```

Proposed actual acquisition:

```bash
.venv/bin/python -m src.acquisition.travel \
  --config configs/travel_insurance/sources.json \
  --output-root outputs/travel_insurance/acquisition
```

Proposed single-insurer debugging:

```bash
.venv/bin/python -m src.acquisition.travel \
  --config configs/travel_insurance/sources.json \
  --provider allianz \
  --dry-run
```

The CLI should show clear stages and a final summary without printing PDF bodies,
credentials, or unnecessary complete error pages. This PRD does not mandate a
module name; consult the approved architecture owner map before implementation
and avoid confusing acquisition with schema discovery in `src/run.py`.

## 15. Example product-release contract

```json
{
  "product_release_id": "allianz:travel_insurance:2026-01-01:ab12cd34",
  "provider_code": "allianz",
  "product_family": "travel_insurance",
  "product_types": [
    "international_single_trip",
    "annual_multi_trip",
    "domestic"
  ],
  "effective_from": "2026-01-01",
  "effective_to": null,
  "status": "current",
  "documents": {
    "primary_pds": ["sha256:pds..."],
    "supplements": ["sha256:spds..."],
    "benefit_summaries": ["sha256:benefits..."],
    "brochures": ["sha256:brochure..."]
  },
  "relationship_ids": ["relationship:..."],
  "association_status": "confirmed",
  "evidence": [
    "Documents appear in the same official current-policy section",
    "SPDS explicitly references the base PDS effective date"
  ]
}
```

`primary_pds` is an array to permit bundles with multiple primary terms files.
The MVP normally has one, but the contract should not preclude reasonable
multi-document products.

## 16. Review queue

Each item should contain candidate document/relationship ID, system proposal and
confidence, source page/final URL, section/anchor/date context, PDF title/effective
date/text evidence, and conflicts such as a current page label beside a newer PDS.
Decisions are accept/reject/edit with reviewer, timestamp, and rationale.

Keep generated queues immutable and decisions separate. Overwriting a queue loses
the distinction between the system's original proposal and the human's final decision.

## 17. Safety, compliance, and legal boundaries

### 17.1 Website access

Visit only configured official public URLs; respect robots.txt, rate limits, and
website responses. Do not bypass controls or conceal automation. Do not enter
quote, account, or claims-submission workflows.

### 17.2 Content use

Public download availability does not establish permission for commercial
redistribution. The draft cites [Allianz Terms of Use](https://www.allianz.com.au/terms-of-use.html)
as an example. The default MVP treats PDFs as internal research inputs. Before
enterprise launch, customer delivery, or a public dataset, business/legal owners
must establish whether automated download, long-term retention, fact extraction,
third-party display of text/screenshots/full PDFs, and required notices/source
links are permitted.

### 17.3 Data security

Collect no personal information. Ordinary logs must not contain environment
variables, credentials, PDF body text, or raw error responses.

## 18. Dependency policy

Prefer existing dependencies and the standard library. Additions require approval
and an updated dependency policy.

- Static HTML: first determine whether existing/standard-library tools suffice.
- Stronger selection: evaluate httpx/BeautifulSoup explicitly; do not rely on
  incidental transitive installation.
- Dynamic pages: consider Playwright only for a demonstrated static-discovery gap,
  with separate approval.
- PDF metadata/text: reuse the integrated PDFingestor/parsing boundary rather than
  add another parser.

This limits migration cost and avoids adding a browser runtime merely because
crawlers often use one.

## 19. Testing strategy

### 19.1 Unit tests

Cover URL normalization/domain/private-IP rejection; hints/type classification;
dates/current/archive decisions; SHA-256 deduplication/naming; PDS/SPDS/Brochure
relations; confidence/review thresholds; and stable error codes.

### 19.2 Fixture integration

Use minimized saved HTML and fake PDF bytes for direct PDF links, one-hop document
centers, separate current/archive sections, multiple URLs per PDF, changed content
at one URL, off-allowlist redirects, PDF URLs returning HTML, and partial success
after insurer failure. Run offline by default so CI does not depend on website
structure or network reliability.

### 19.3 Contract tests

Validate all acquisition runs, manifests, releases, relationships, and review
artifacts against authoritative JSON Schema. Reject unknown fields, invalid enums,
missing provenance, and automatic relationships without evidence.

### 19.4 Golden dataset

Create a small human-confirmed dataset for the three proposed insurers: expected
current documents; excluded archived/FSG/claim forms; correct document/product
types; and PDS/SPDS/Brochure relationships. Use it to measure the proposed 95%
coverage and association-precision targets.

### 19.5 Live smoke tests

Require explicit opt-in and visit only a few official pages. Acceptance: robots/
allowlist checks pass; at least one valid candidate or an explainable
`NO_DOCUMENT_CANDIDATES`; no off-config domain access; no quotations/forms.
Offline success cannot be described as verified website acquisition. Only an
actual live smoke run supports that statement.

### 19.6 Repository checks

The proposed implementation should at least run:

```bash
.venv/bin/python -m compileall src tests
.venv/bin/python -m unittest discover -s tests
```

Also exercise new CLI `--help`. Current offline instructions additionally unset
the live-test database variable; see the operator guide.

## 20. Monitoring and run reports

Summarize insurers and success/partial/failure counts; pages, candidates,
downloads, duplicates, skips, and failures; counts by document type and version
status; added/changed/removed/unchanged documents; automatic associations and
high/medium/low confidence; error-code counts; total bytes and duration.

These support diagnostics and product KPIs. A sudden zero candidate count may
indicate a website redesign, not withdrawal of the insurer's products.

## 21. Proposed implementation phases

### Phase 0: Requirements and legal scope

Review the PRD, confirm insurers/internal use, and decide whether browser tooling
and new dependencies are allowed. Access/content boundaries affect design and
should be settled before coding.

### Phase 1: Contracts and static acquisition core

Define registry, artifact contracts, and error codes. Implement URL/robots checks,
static discovery, streaming download, PDF validation, hashing, and manifests, with
offline fixture tests. Done when generic acquisition works end-to-end on fixtures
without an insurer-specific implementation.

### Phase 2: Three insurer configurations

Configure Allianz, Cover-More, and SCTI, with golden expectations and opt-in live
smokes. Target at least 95% discovery coverage of current core documents.

### Phase 3: Versions and associations

Extract titles/effective dates; implement releases, relationship resolution, and
review queues; test many-to-many PDS/SPDS/Brochure scenarios. Target at least 95%
high-confidence precision against human-labelled relationships.

### Phase 4: Pipeline integration

Let sampling read confirmed current releases. Add Travel contracts/business
checks and verify vertical isolation across discovery, extraction, analysis, and
refinement. Preserve default Health behavior while allowing explicit Travel config.

### Phase 5: Updates and optional dynamic pages

Add run comparisons, website-change alerts, and dynamic adapters only for insurers
with demonstrated need.

## 22. Proposed MVP acceptance

### Acquisition

- [ ] At least three insurer configurations pass contract validation.
- [ ] HTTPS and allowlists are enforced, including redirects.
- [ ] Follow depth/page count have hard limits.
- [ ] Every saved file passes PDF signature and size checks.
- [ ] Identical SHA-256 content is not saved twice.
- [ ] One insurer failure produces partial success without losing others' results.

### Classification and association

- [ ] PDS, SPDS, Benefits Summary, and Brochure are distinguished.
- [ ] An SPDS can link to one or more PDS files.
- [ ] A document can apply to several product types.
- [ ] Current/archive decisions retain page/text evidence.
- [ ] Ambiguous relationships enter review.
- [ ] Confirmed documents form a validated release artifact.

### Auditability

- [ ] Every candidate has source page, discovered/final URL, and status.
- [ ] Saved files have SHA-256, timestamp, size, and Content-Type.
- [ ] Failures/skips use stable codes.
- [ ] Human decisions are stored separately from immutable queues.

### Engineering quality

- [ ] No raw PDFs, outputs, usage logs, or credentials are committed.
- [ ] No unapproved dependencies are added.
- [ ] Compileall and offline tests pass in a clean checkout.
- [ ] CLI help works without network access.
- [ ] README, architecture, and project index follow public workflow changes.

## 23. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Website redesign | Missing candidates/misclassification | Config-driven rules, fixtures, count monitoring, insurer isolation |
| Incorrect current/archive state | Outdated terms downstream | Page context, dates, supersession, review on uncertainty |
| Missing SPDS association | Incomplete applicable terms | Explicit `amends` relationships and bundle completeness checks |
| Brochure/PDS conflict | Unreliable comparisons | PDS/SPDS authority over summaries; source-level evidence |
| Dynamic-page maintenance | Heavier local/CI runtime | Static-first, explicitly enabled per insurer |
| Commercial-use restrictions | Enterprise legal risk | Internal MVP; per-insurer legal review before release |
| Redirects to unofficial sites | Security/provenance risk | Allowlists and address validation before/after requests |
| Leaking Health assumptions | Wrong Travel classification/evaluation | Vertical-specific contracts, taxonomies, and checks |
| Moving integration baseline | Merge conflicts | Small short-lived increments; rebase after baseline integration |

## 24. Questions pending product review

1. Are PDFs internal research inputs only, or will customers view/download them?
2. Should archived PDS content be retained, or only metadata/URLs?
3. Is TMD downloading in the first release?
4. Confirm Allianz, Cover-More, and SCTI, or replace one?
5. Who reviews relationships, and how quickly?
6. Is acquisition manual, weekly, or monthly?
7. Do PDS/SPDS always take precedence over conflicting website summaries?
8. Are new HTML dependencies allowed; if not, is standard-library maintenance acceptable?
9. Must the MVP include field-level attribution to PDS, SPDS, or Brochure?

## 25. External references

Historical references; this translation does not revalidate them:

- [Moneysmart: Travel insurance](https://moneysmart.gov.au/other-types-of-insurance/travel-insurance)
- [Allianz Travel Insurance](https://www.allianz.com.au/travel-insurance.html)
- [Allianz Policy Information](https://www.allianz.com.au/my-allianz/policy-information.html)
- [Cover-More PDS](https://www.covermore.com.au/pds)
- [SCTI Comprehensive](https://scti.com.au/our-policies/comprehensive)
- [SCTI Annual Multi-Trip](https://scti.com.au/our-policies/annual-multi-trip)
- [SCTI Domestic Policy Wording](https://scti.com.au/our-policies/domestic/policy-wording)

Pages change. Recheck pages, robots.txt, and terms when implementing configurations
or live smokes; these URLs are not permanent stable interfaces.

## 26. Proposed decision

Approve the following direction for technical design:

1. Start with three insurers, three product types, and four core document types.
2. Associate PDS, SPDS, Benefits Summary, and Brochure through `product_release`.
3. Use official pages, static-first discovery, at most one followed level, and strict allowlists.
4. Require human review of low-confidence versions/relationships; prioritize PDS/SPDS over brochures.
5. Separate acquisition from LLM/discovery through manifests and versioned JSON contracts.
6. Use the originally proposed `feat/travel-insurance` branch and `origin/merged-pipeline` baseline only if still approved at implementation time.

The original proposal required approval before implementation. Changes to business
scope, insurers, legal boundaries, or taxonomy require updating the PRD first.
This remains a historical draft, not new instructions to implement its proposal.
