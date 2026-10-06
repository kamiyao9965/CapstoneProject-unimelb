# Car Insurance vertical：项目计划与运行手册

[中文原文 / Chinese original](#chinese-original) · [English version / 英文版](#english-version-text)

<a id="chinese-original"></a>

状态：第一阶段已完成，127 个相关离线回归测试通过；真实数据 discovery 和 benchmark 尚未开始。
Started: 2026-09-21. Base: origin/main c439a95. Branch: feat/car-insurance.

当前成果是可运行的新 vertical 接入和数据登记工具。实际测试数据、人工审核后的
schema、准确率报告及数据库入库仍属于后续阶段。原下载数据集目前只有 private_health。
请在 IDE 中打开 `.worktrees/car-insurance`，该目录是新的独立 Git worktree。

## 中文执行计划

| 阶段 | 主要任务 | 验收结果 | 进度 |
| --- | --- | --- | --- |
| 1. 接入引擎 | 新分支、manifest、三个 prompts、CLI/UI 接入、多产品抽取测试 | 模拟 discovery/extraction 跑通，旧流程回归通过 | 已完成 |
| 2. 建立数据集 | 收集官网 PDS/SPDS、登记版本和来源、检查表格、按模板/版本分组划分数据集 | 约 15–25 份独立 PDS、至少 5 个来源/模板族、覆盖三种险种；数量以实际可用来源为准 | 来源入口与登记工具已完成，PDF 采集待做 |
| 3. 发现与人工审核 schema | 开发集 discovery、三轮 consensus、审核字段及 taxonomy、独立 holdout 验证 | 留存审核决策、版本和来源；明确金额单位/范围及可选保障 | 待数据 |
| 4. 人工标注与 benchmark | 对测试产品标注、第二人复核、实现评测字段映射及指标 | 锁定测试集，报告宏/微平均、可比较样本数和失败率 | 待 schema 与标注 |
| 5. 优化与发布 | 用开发集定位错误、冻结 baseline、审核 canonical schema、接入存储 | 可复刻命令、报告、经批准的 schema 和验证过的存储映射 | 待评测 |

下一项具体工作：从 source_register.json 中的官网入口收集首批真实 PDS，填入
intake CSV 并运行 dataset 工具，先检查表格质量，再执行付费 discovery。
本次没有调用真实模型，没有创建虚假的 approved schema 或测试准确率。

验证命令（在本 worktree、使用父目录现有 Python 环境）：

```powershell
& ..\..\.venv\Scripts\python.exe -m unittest tests.test_car_insurance tests.test_vertical_manifest tests.test_travel_schema_migration tests.test_tool_ui tests.test_tool_app tests.test_loop tests.test_consensus tests.test_review tests.test_run
```

2026-09-21：127 tests passed。为运行最新版 main 的 Travel 回归，父目录 `.venv`
补装了仓库 requirements.txt 声明的 SQLAlchemy 和 psycopg；测试没有连接真实数据库。

## 2026-10-01 数据准备进展

已登记 8 份真实 PDS：development 5 份（AAMI/QBE/Youi）、holdout 1 份（NRMA）、test 2 份（Allianz）。
原件保留，复制件和哈希校验完成；开发集 289 页已离线解析。抽查确认 Youi 保障状态符号丢失、QBE 提示框误识别并截断正文，
因此 schema discovery 尚未开始，先处理解析质量门槛。阶段 2 部分完成，版本适用性和补充文件审核仍待做。
本节更新优先于上方初始状态表中的“采集待做”；详见 [首批登记与解析检查](car-insurance-intake-v1.md)。

## Scope

2026-10-06 重跑：AAMI Third Party、Youi 均通过。全保单责任池检查已扩展（AAMI Comp 产物因此被标出）。Youi 两个低档位的一般除外被写成 []（原文写明适用于所有部分），检查器没有拦住，待定规则。195 项离线测试通过。

2026-10-06 其余 4 份开发文档各跑一次：QBE Comprehensive、AAMI Comprehensive 通过；AAMI Third Party 失败（1 处真实例外遗漏、1 处半句引用，另有 2 条已修复的页码误报）；Youi 因请求卡住约 12 小时、无响应而中止。AAMI Comp 的 2,000 万责任上限没有建共享池，待处理。193 项离线测试通过。详见 [4 份开发文档报告](car-insurance-extraction-dev-v4r4.md)。

2026-10-06 取值口径（用户确认）：含 market_value 的额度用 per_vehicle；例外范围只允许 (own_vehicle_damage, third_party_liability)/(both, none)/(source_defined, source_defined)。已加入 GUIDANCE 和路径化检查，191 项离线测试通过；两次复跑产物在新口径下各有 7、2 条不符。待决定：是否付费扩到其余 4 份开发文档。

2026-10-06 离线改进：missingness/benefit_uniqueness 报错给出字段与 ID；一般除外检测器覆盖 AAMI 编号章节（AAMI 两份由 0 增至 29 条，QBE 清单不变）。188 项离线测试通过，零调用。待决定：池 basis 与例外 scope 口径；是否付费扩到其余 4 份开发文档。

2026-10-06 方案 A 下同样本两次独立复跑：**2/2 通过**完整校验并生成产物（均 10→1→0），S1–S5、两个共享池和例外引用两次一致。run1 有 1 处例外 condition 语义错误；池 basis 等口径需人工确定。仍为候选 schema，n=2 单文档。详见 [两次复跑报告](car-insurance-extraction-sample-v4r3.md)。

2026-10-05 例外证据方案 A：父规则保留完整原文块，例外只需逐字引用含提示的完整句子/条目（跨块时可引完整下一块），报错给出需引原句。185 项离线测试通过；r2 第 3 轮零调用回放 5→4 条，剩余为 1 处真实遗漏及 2 处半句引用。尚未付费复跑。

2026-10-05 v4 + r2 同样本复跑：3 次生成后仍失败（10→5→5 条诊断），无成功产物。表格、共享责任池、Fire & Theft 共同赔付池、S1/S3/S4/S5 均修好并稳定保持，无回退；
剩余为 4 条例外证据放置约定问题（语义正确）与 1 处真实例外遗漏（轮胎/机械故障），两轮修复未动。下一步需先决定例外证据约定。详见 [r2 复跑报告](car-insurance-extraction-sample-v4r2.md)。

2026-10-05 运行时改进 r2：明确表格约定，v4 修复保留限长候选/错误历史，合并路径化诊断，改进证据归属检查，加入单产品共享额度关系回归。
旧 v4 三轮失败均可离线重现并检出共享池缺失，v3 正确共享关系保留；零 API 调用，未动 holdout/test。
当前下一步是同一开发样本受控复跑，详见 [诊断与修复 r2](car-insurance-extraction-diagnostics-r2.md)。

2026-10-05 v4 真实重跑：同一 QBE TPPD 开发 PDF，3 次生成后校验仍失败，没有成功产品产物。
S1–S5 部分内容改善，但表格/证据约定不稳定，且共享额度池回退；暂不扩大批量或使用 holdout/test。
下一步先改进离线诊断、修复上下文和共享额度回归。详见 [v4 重跑与对照](car-insurance-extraction-sample-v4.md)。

2026-10-05 修复更新：针对首次抽样 S1–S5，新增 review_v4 候选稿、例外范围、换车保障字段及
与实际输入绑定的原文完整性检查。保留 v3 结果；真实重跑及效果确认尚待完成。
详见 [抽样问题修复 v4](car-insurance-extraction-fixes-v4.md)。

2026-10-03 真实抽样更新：review_v3 已对 QBE TPPD 开发文档试抽取成功，1 次 gpt-5 响应、无修复重试。
结构与业务校验通过，但内容抽查发现除外例外条件、全局规则完整性及专用字段漏填等问题；
不代表 schema 已批准或 benchmark 达标。详见 [首次抽样报告](car-insurance-extraction-sample-v3.md)。

2026-10-03 人工意见后续：已实现 review_v3 候选结构，补分类定义、产品级共享额度池/子限额、
百分比与组合限额、显式文档定义和规则清单、自付额适用范围；v2 保留，未批准或进行付费抽取。
当前审核入口见 [Schema 修订 v3](car-insurance-schema-review-v3.md)，优先于下方 v2 历史说明。

2026-10-03 schema 修订更新：四项审查已形成 `schema_review_v2.json` 候选稿，
包含字段归属、AUD 限额范围、产品/附加险边界及可执行嵌套约束。原始草稿未覆盖，
未调用模型，未将修订稿标成人工批准版本。详见 [Schema 修订 v2](car-insurance-schema-review-v2.md)。

2026-10-03 schema 试跑更新：用户确认当前 v8 解析可用于试跑后，已完成 openai/gpt-5 单次 discovery，
产物为 `outputs/car_insurance/schema_trial_20261003/schema_draft.json`（31 字段、30 保障分类）。
原警告保留，通过绑定 PDF/配置/解析内容指纹的人工试跑记录放行；不是 schema 审批。
尚未运行 consensus 或真实产品抽取，详见 [Schema 试跑记录](car-insurance-schema-trial.md)。

2026-10-03 最新：综合险概览页与明确续页关联已修复；车险模型调用前增加质量门槛。
最终结果 `parse_check_v8`（parser v9），146 项选定离线回归通过。质量报告仍列出 88 页待复核项，
5 份开发文档均未自动放行，schema discovery 未启动；不要用先前测试通过数替代全文质量审核。
详见 [第四轮修复与剩余问题](car-insurance-parser-fixes.md)。

第三轮更新：AAMI 第三方险第 5 页保障对比表已恢复并逐行对照原图；最新结果 `parse_check_v6`，
解析器 v8，138 项选定离线回归通过。旧结果保留，尚未运行 schema discovery。

第二轮更新：用户发现 AAMI 侧栏标签串入正文，已实施几何分区修复，最新输出为 `parse_check_v5`，
解析器 v7，135 项选定离线回归通过。旧 v3 不作为 schema 输入；跨页语义与第三方险概览表仍需复核。

2026-10-02：已实施首轮解析修复并重跑开发集，131 项选定离线回归测试通过。
已定位的图形状态丢失、正文截断和目录顺序问题得到修复；仍需复核保守降级为正文的页面。
详见 [解析修复报告](car-insurance-parser-fixes.md)。付费 discovery 尚未开始。

Australian private passenger car insurance: comprehensive,
third_party_property_damage, third_party_fire_and_theft. CTP bodily-injury cover,
commercial fleets, motorcycles, warranty and travel rental-excess products are
outside v1. This is a document-extraction project, not a quote engine.
The initial classification follows [ASIC Moneysmart's car insurance overview](https://moneysmart.gov.au/car-insurance/choosing-car-insurance).

One PDS can yield multiple separately named product/tier records. Phase 1 operates
on individual PDS documents. It does not merge PDS/SPDS into a current contract:
SPDS/PED/TMD/FSG are registered as supporting evidence for human review, excluded
from the default `pds` input category. Do not report an older PDS alone as current
cover if a supplement amends it.

## Milestones

| Phase | Deliverables and work | Exit criteria | State |
| --- | --- | --- | --- |
| 1: integration | Manifest, three prompts, shared runtime/UI registration, offline tests, dataset intake utility | Car schema discovery and multi-product extraction pass with injected providers; unavailable operations are gated | Implemented; see test results in handoff |
| 2: corpus | Collect official PDS/SPDS; record source/retrieval/release; hash PDFs; review table quality; assign development/holdout/test groups | Aim for 15–25 distinct PDS across at least 5 insurer/template families and all 3 cover types, subject to actual availability; inventory validated | Seed pages and intake tool ready; acquisition pending |
| 3: schema | Discover from development only; 3-run consensus; review fields AND coverage taxonomy; apply decisions; inspect unseen holdout output | Human-reviewed schema, version/hash, review decisions and evidence; no pending decisions; units/optionality agreed | Pending real corpus |
| 4: benchmark | Annotate holdout/test products with page evidence; second reviewer adjudicates; implement car-specific evaluator/adapter | Fixture tests for aliases, optionality, unlimited/null, excess vs cap, product matching; frozen labels and split; macro/micro and end-to-end metrics | Pending labels and evaluator |
| 5: release | Freeze baseline, fix evidenced errors on development, approve canonical schema, compile storage mappings, publish runbook | Reproducible report and approved contract; storage validation passes before enabling storage | Pending |

Suggested ownership: vertical owner curates the corpus and makes schema decisions;
a second domain reviewer adjudicates labels; engineering owner maintains adapters
and tests. Assign actual team members before annotation. Phases 3–5 depend on data
and human review; these are not represented as already completed.

## What exists now

- `configs/car_insurance/manifest.json` auto-registers discovery/refinement/extraction
  in the shared CLI and Streamlit console. A new hard-coded contract catalog entry
  is unnecessary: `car_insurance/discovered_schema` is compiled from the manifest.
- The prompts cover identity, covered events, property liability, sum-insured basis,
  excesses, hire car, glass, towing, new-car replacement, repairs, options,
  eligibility, exclusions and source evidence. Fields remain evidence-driven.
- `src/car_insurance/dataset.py` records SHA-256 and rejects split leakage by exact
  bytes and manually assigned insurer/release group. It does not move/download files.
- `tests/test_car_insurance.py` uses synthetic schemas/products solely for offline
  integration checks. There is no approved Car business schema or accuracy result.
- `acquisition`, labelled `evaluation`, and `storage` are disabled until implemented.
  `source_register.json` lists official entry pages; it is not a crawler config.

Legacy discovery schemas describe list[object] item shapes in prose. The car
review_v2 candidate now supplies executable item_schema definitions, closed nested
objects, and exact `_unfilled`/null consistency checks. Legacy schemas retain their
existing behavior. These offline integration tests do not demonstrate real-data
extraction accuracy or constitute human approval.

## Corpus and leakage rules

Start from the recorded [Allianz policy documents](https://www.allianz.com.au/my-allianz/policy-information/policy-documents.html)
and [AAMI third-party documents](https://www.aami.com.au/policy-documents/third-party-car-insurance).
These entry pages were checked on 2026-09-21; PDF release applicability must be
checked when downloading. Expand to additional insurer/template families during
phase 2; do not treat two brands using identical wording as independent templates.

Arrange files as follows (actual PDFs remain gitignored):

```text
data/car_insurance/
  development/<insurer>/pds/<filename>.pdf
  development/<insurer>/spds/<filename>.pdf
  holdout/<insurer>/pds/<filename>.pdf
  test/<insurer>/pds/<filename>.pdf
```

Copy the header-only `configs/car_insurance/intake_template.csv` to
`outputs/car_insurance/intake.csv` and fill one row per PDF. Columns:
relative_path, insurer, release_group, split, document_type, source_url,
retrieved_at (ISO date). Paths are relative to data/car_insurance.
Use the SAME insurer/release_group for related PDS/SPDS and near-identical historical
versions. Ideally allocate entire insurer/underwriter template families to one split.
The tool cannot detect near-duplicate wording or incorrect human group assignments.
It validates registered rows, not completeness of all files on disk: only supply
registered, reviewed PDS files to runs. Duplicate copies within a split should be
removed from sampling; inspect documents versus unique_pdfs in the inventory.

Suggested starting allocation: about 60% development, 20% holdout, 20% locked test
by independent groups, not by PDF page or product row. Every product in a multi-plan
PDS belongs to the same split. Discovery, prompt tuning and consensus must never
see locked test PDFs or their labels. Once test errors guide changes, retain the
old score as a diagnostic baseline and use new untouched documents for final claims.

## Run from the new worktree (Windows PowerShell)

Open `.worktrees/car-insurance` as the IDE folder. From this folder reuse the parent
environment while the bootstrap is being tested:

```powershell
$CarPython = (Resolve-Path ..\..\.venv\Scripts\python.exe).Path
& $CarPython -m unittest tests.test_car_insurance
& $CarPython -m src.car_insurance.dataset --input-root data/car_insurance --assignments outputs/car_insurance/intake.csv --output outputs/car_insurance/inventory_v1.json
```

The inventory command rejects an existing output file; use a new versioned filename.
The original Health checkout and its `.env`/outputs remain in the parent directory.
The engine reads process environment variables, not `.env` automatically. To use
the existing credential configuration without copying it, load selected variables:

```powershell
Get-Content ..\..\.env | ForEach-Object {
  if ($_ -match '^(OPENAI_API_KEY|MY_OPENAI_API_KEY|ANTHROPIC_API_KEY|DEEPSEEK_API_KEY|LLM_PROVIDER|LLM_MODEL|LLM_DOCUMENT_INPUT)=(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim().Trim('"').Trim("'"))
  }
}
```

This simple loader expects one KEY=value per line, without inline comments.
After real development PDFs are registered, the following invokes billable models
and stops for human review (no LLM calls are part of the offline tests):

```powershell
& $CarPython src/refine/loop.py --manifest configs/car_insurance/manifest.json --input-root data/car_insurance/development --per-category 3 --consensus-runs 3 --review-ui --out-dir outputs/car_insurance/refine
& $CarPython -m streamlit run src/review_app.py -- --manifest configs/car_insurance/manifest.json --consensus-dir outputs/car_insurance/refine/round_1/consensus
```

Apply all decisions in the UI, including the separate taxonomy review where needed.
The first round may be numbered higher if a prior attempt exists. Use its actual
round directory. Once reviewed_schema.json exists:

```powershell
& $CarPython src/refine/loop.py --manifest configs/car_insurance/manifest.json --resume-review outputs/car_insurance/refine/round_1 --input-root data/car_insurance/development --out-dir outputs/car_insurance/refine
```

The loop's built-in sample exclusion is a development diagnostic, not the separate
grouped holdout above. Use final_schema.json and the reserved holdout for the first
manual application review (billable extraction):

```powershell
& $CarPython src/run.py batch --manifest configs/car_insurance/manifest.json --schema outputs/car_insurance/refine/final_schema.json --input-root data/car_insurance/holdout --output-dir outputs/car_insurance/holdout_extractions
```

Do not add `--evaluate`: the Health labelled evaluator does not evaluate car
insurance. No accuracy number is available until phase 4. The shared CLI prints
actual paths; preserve output, schema, source hash and model/parser versions.

## Review and benchmark acceptance checklist

Human schema approval and human ground-truth annotation are separate tasks. For
each gold product, record product/release identity, benefit category, status
(included/optional/excluded/unknown), cap amount/unit/scope, excess applicability,
eligibility conditions and source page/section. Review table continuation and
merged headers against rendered PDF pages. Record reviewer, date and disputes.
Do not use model-generated records as unreviewed gold labels.

Before benchmarking, test mapping for every approved field and nested key: the
Health walkthrough showed that an adapter can silently omit numeric waiting
periods/shared limits or mistake per-visit amounts for annual caps. For Car,
explicitly test per-day hire car vs total cap and excess vs indemnity limit.

Report product matching, coverage status accuracy, benefit precision/recall,
limit and excess value accuracy, units/scopes, exclusions/conditions, and extraction
failure rate. Publish comparable counts, macro/micro and by-product-type/insurer
breakdowns; unknown/unverifiable claims must not count as correct. Choose quality
thresholds with the team after label coverage is known; never tune them to a score.

Parser improvements should follow a traced error: PDF -> parsed table -> model
JSON -> evaluation adapter -> gold comparison. Compare PDFingestor and optional
MinerU only on fixed development examples before selecting a parser for the
locked test run. Do not change the parser midway through the baseline.

<a id="english-version-text"></a>

## English translation of the Chinese execution plan and historical updates

This supplements the English Scope/phase/runbook sections already present above. Historical status statements refer to their dated stages, not current acceptance. Latest completed targeted rerun: see [post-review run report](car-insurance-postreview-rerun-20261006.md); further runs are paused.

Initial state,2026-09-21: phase1 complete,127 relevant offline regressions passed; real discovery/benchmark not started. Branch feat/car-insurance began from origin/main c439a95. Deliverables were runnable vertical integration and registration tools; real data, reviewed schema, accuracy reports and storage were future work. Only private_health downloads existed then. Open .worktrees/car-insurance as the independent IDE worktree.

The Chinese phase table specifies:
1. Engine integration: branch/manifest/three prompts/CLI+UI/multiproduct tests; mocked discovery/extraction and old regressions pass(completed).
2. Dataset: official PDS/SPDS, version/source registration, table checks, grouped template/version splits; target15–25 independent PDS across at least five source/template families and three cover types, subject to availability. Source entry points/tools completed, downloads pending then.
3. Development discovery, three consensus rounds, field/taxonomy review and independent holdout; retain decisions/versions/provenance, amount scope and options; waiting for data.
4. Test-product labelling with second reviewer, evaluation mappings/metrics, locked test set, macro/micro/comparable counts/failure rate; waiting for schema/labels.
5. Development optimisation, frozen baseline, canonical approval/storage, reproducible commands/reports/approved mappings; waiting for evaluation.

Initial next task: collect official PDFs from source_register.json, fill intake CSV, run dataset tooling and table checks before paid discovery. No real calls, invented approval or accuracy claims then. The test command above uses the parent venv;127 passed. SQLAlchemy/psycopg declared in requirements were installed to run main's Travel regressions; no live DB.

Oct1: eight PDS registered, five AAMI/QBE/Youi development, one NRMA holdout, two Allianz test. Originals retained/copies hashed,289 development pages parsed. Youi symbols and QBE callout truncation blocked discovery; phase2 partial, applicability/supplements still pending. This superseded the initial “downloads pending” state.

Historical updates, newest-first as above:
- Oct6 AAMI TP/Youi reruns passed then; extended liability checks flagged AAMI Comp; Youi lower tiers wrongly had no exclusions despite all-policy wording.195 tests.
- Oct6 other four first runs: QBE/AAMI Comp passed, AAMI TP genuine exception/half-citation failures plus two fixed footer false positives, Youi~12h infrastructure abort/no response. AAMI20m missing pool;193 tests.
- User-confirmed market_value→per_vehicle and three scope pairs entered GUIDANCE/path checks,191 tests; old two QBE runs now7/2 issues, expansion decision pending.
- Missingness/uniqueness gained fields/IDs; AAMI numbered exclusions0→29 each, QBE unchanged,188 tests/no calls; basis/scope and expansion decisions pending.
- Convention-A same-QBE runs2/2 passed10→1→0, S1–S5/pools/citations agreed; run1 condition semantic error and basis decisions remained, n=2/candidate only.
- Oct5 conventionA retained full parent block, full cue sentence/bullet or complete continuation for exception, exact required text in errors;185 tests; r2 final replay5→4, real omission+two partial citations; no paid rerun yet.
- v4+r2 three calls failed10→5→5, no product; table/pools/S1/S3/S4/S5 retained without regression; exception evidence convention and tyre/mechanical omission stalled. The detailed report distinguishes diagnostic counts from semantic issues.
- r2 runtime clarified tables, bounded latest candidate/history, aggregate paths, evidence ownership and single-product shared relations; old failures replayed, v3 correct pools preserved, no calls/holdout/test; controlled same-sample run next.
- v4 real three-call trial failed, no accepted output; S1–S5 improved partly but table/evidence/pools regressed; offline fixes before expansion.
- v4 candidate added scoped exceptions/change-of-car/source checks for S1–S5, keeping v3, real verification pending then.
- Oct3 v3 QBE sample passed one gpt-5 call/no repair, but content review found exception/global-rule/dedicated-field omissions; not approval/benchmark.
- v3 review response added definitions/product pools/sub-limits/percentages/combinations/document schema/rules/excess scope, preserving v2, unapproved/no calls; its review entry supersedes v2.
- v2 candidate established ownership/AUD scope/product-option boundaries/executable nesting; original draft unchanged, no call/approval.
- First discovery after user accepted v8 trial risks produced31 fields/30 categories using gpt-5; fingerprint-bound parser permission retained warnings, not schema approval; no consensus/extraction yet.
- Parser v9/parse_check_v8 restored overview/continuations and pre-call gate,146 tests;88 warning pages/five blocked documents, no discovery at that stage.
- Third parser round: AAMI TP page5 restored and visually checked,parse_check_v6/v8,138 tests, no discovery.
- Second: geometric AAMI sidebar recovery,parse_check_v5/v7,135 tests; v3 not schema input, continuation/overview still pending.
- Oct2 first fixes: symbols/text truncation/contents order repaired,131 tests; degraded pages need review, paid discovery not started.

All commands, templates, identifiers and English phase acceptance criteria earlier in this document remain unchanged.
