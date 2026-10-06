# v5 审核修复与超时结果找回（2026-10-06）

[中文原文 / Chinese original](#chinese-original) · [English version / 英文版](#english-version-text)

<a id="chinese-original"></a>

后续：用户已完成复核；审核归档与路径诊断修复见 [人工复核后实施记录](car-insurance-post-review-20261006.md)。本文保留上一轮的历史状态。

本轮实施了跨块例外检查修复、具名档位的拖车责任回归、分类回放工具及只读超时查询。
**没有新建模型生成请求，没有取消后台任务，没有修改历史响应/schema/运行状态，没有读取 holdout/test。**

## 1. 跨块例外：不能把下一条除外当续文

文件：`src/car_insurance/source_coverage.py`。

旧 `following_blocks` 只取下一个非导航块。在 AAMI Hire 条款中，它错误地指向了 `Incorrect fuel usage`；
单独引用“加错燃油”的整块文字，曾能让 Hire 的例外证据检查通过。这是已用反例确认的**检查器缺陷**。

现在只有父块真正以例外引导冒号结束、后一块为可接续的条件项目列表、页码相邻时，才允许使用下一块作为引用替代。
父块内部已经有条件列表时不会继续借用下一条款；新除外句、未知布局、过远页码不自动关联。
保留 QBE `except when: → • it is / you have ...`、Youi `unless: → • you had ...` 等已确认的条件接续形式。

这是保守的文本结构检查，不是完整的语义续页引擎。不支持的写法仍需完整引导句证据和人工复核，不通过模糊相似匹配放行。
已增加原问题反例、内嵌列表、负面新条款、跨页距离和合法接续正例测试。

## 2. Youi 拖车责任：从待抽查升级为已定位遗漏

文件：`src/car_insurance/extraction_diagnostics.py` 的 `trailer_liability_issues`。

检测需要同时满足明确的 Legal Liability 章节、列明适用产品名称、正向的拖带 trailer/caravan 责任说明。
只检查原文明确列出的产品，不把其他档位默认当作适用。拖车本身损坏除外不等于拖带时的第三方责任除外。

当原文明示每次 claim 的共同上限时，成员还必须互链正确的 `per_claim/aggregate` 额度池；不强行套用 QBE 的 `per_incident`。
没有硬编码 Youi 名称、固定页码或保额；未匹配的措辞和未能解析的适用关系不擅自推断。

回放 v5 Youi 第三轮：三个档位各报一条 `trailer_liability_missing`，原产物不再符合当前检查。
原先 42 条共享规则仍保留其意义，但一般除外清单覆盖不代表所有保障类别完整。

## 3. 诊断分类与审核区分

文件：`extraction_diagnostics.py::classify_diagnostic`、`src/car_insurance/audit_saved_trials.py`。

- `structured_content_missing`：专用字段或明确来源关系缺失，例如本轮拖车责任。
- `evidence_issue`：引用不完整/不匹配、例外证据不满足约定；**不能自动解释为语义遗漏**。
- `coverage_review`：来源映射不足，需要复核是否真漏抽。
- `structure_or_business_rule`：字段结构或跨字段约束。

检查器缺陷不能只靠一条报错自动判断，单独记录有反例证明的 `checker_defect`。
代理的定向语义复核另存 `agent_review.json`，不是人工 gold 或用户审批。AAMI TP 的 Extra costs 是 exceptions 字段缺失；
Hire 的两个例外已表达，主要是证据不完整。这两种情况不能因同样报 `full source evidence` 就混为一类。

新工具只读保存的响应，先过 JSON 结构检查再执行运行时诊断，校验输入哈希，输出到必须不存在的新目录。
本轮回放了 v5 的全部 13 个已保存响应及 3 个找回响应。

## 4. 超时请求实际上都已完成

按 [官方 Background mode 文档](https://developers.openai.com/api/docs/guides/background) 使用 `responses.retrieve` 查询已有 ID。
新增 `src/car_insurance/inspect_timeout.py`：只允许有 TimeoutError/response ID 的 development 记录；只读查询、30 秒网络超时、禁用自动重试。
凭据仅从显式传入的 env 文件或现有环境读取，不输出密钥。结果保存到新目录；原失败日志不变。

2026-10-06 20:38（Sydney）查询时，三个请求均为 `completed`。下表耗时来自服务端 `completed_at - created_at`：

| 原运行 | 原本地等待上限 | 服务端实际耗时 | 本次找回的原请求 tokens | 当前离线检查 |
| --- | --- | --- | --- | --- |
| v4r4 AAMI Comprehensive `_rerun` 第 2 次请求 | 900 秒 | 1,206 秒（20:06） | 118,141 | 0 条问题；尚未人工审核 |
| v4r4 QBE TPPD `_rerun` 第 2 次请求 | 900 秒 | 1,584 秒（26:24） | 96,844 | 5 条诊断 |
| v5 AAMI Comprehensive 第 2 次请求 | 1,800 秒 | 2,501 秒（41:41） | 117,930 | 4 条诊断 |

因此这三个案例不是永久卡死；本地在任务完成前停止等待。不能据此确定为什么服务端耗时长，也不能将这一结论推广到此前没有 response ID 的休眠中止案例。
本轮没有扩大模型超时、修改请求内容、重提任务或执行第三轮修复。

新增可核对的历史用量合计 **332,915 tokens**，不是本轮新生成。仅 v5 合计补记为：

`1,456,955 + 117,930 = 1,574,885 tokens`。

v5 AAMI Comprehensive 的已知用量为 `101,238 + 117,930 = 219,168`。这些是 API 返回的 token 用量，不是货币账单。
原 v5 “2 通过、2 失败、1 中止”仍是当时本地运行的记录；不要改成“本次重新跑成功”。

## 5. 当前内容验收状态

| 最新可用响应 | 本轮检查 |
| --- | --- |
| v5 QBE TPPD 第三轮 | 0 条问题 |
| v5 Youi 第三轮 | 3 条拖车责任遗漏 |
| v5 QBE Comprehensive 第三轮 | 5 条证据相关诊断，语义需逐项复核 |
| v5 AAMI Third Party 第三轮 | 2 条诊断；其中 Extra costs 存在结构化例外遗漏，Hire 为引用问题 |
| v5 AAMI Comprehensive 找回的第二轮 | 4 条诊断，未执行第三轮 |

通过检查不等于内容正确或 schema 已批准。找回的 v4 AAMI 响应仍在独立查询目录中，不自动写成历史运行的成功产品文件。

## 文件与复现

- `outputs/car_insurance/timeout_lookup_20261006/lookup_{1,2,3}.json`：已找回输出、状态和用量。
- `outputs/car_insurance/v5_audit_fixes_20261006_verified/audit_report.json`：分类回放、输入与代码哈希。
- 同目录 `agent_review.json`：已确认的检查器缺陷和定向语义复核。
- `tests/test_car_v5_audit_fixes.py`：本轮新增回归与只读查询模拟测试。

最终选定离线回归 **217 项全部通过**（67.699 秒），包含本轮 9 项测试。模型调用为模拟调用，不产生新的模型生成。

```powershell
..\..\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_car_v5_audit_fixes tests.test_car_extraction_diagnostics tests.test_car_schema_revision_v5 tests.test_car_schema_revision_v4 tests.test_car_schema_revision_v3 tests.test_car_schema_revision tests.test_structured_output tests.test_extractor tests.test_extraction_contract tests.test_schema_validation tests.test_car_insurance tests.test_canonical_schema tests.test_travel_schema_migration
```

离线回放示例（目标目录必须不存在）：

```powershell
..\..\.venv\Scripts\python.exe -X utf8 -m src.car_insurance.audit_saved_trials --run-dir outputs/car_insurance/extraction_dev_v5_20261006_youi_car_2026 --lookup-dir outputs/car_insurance/timeout_lookup_20261006 --output-dir outputs/car_insurance/my_v5_audit
```

下一步优先人工复核找回的 v4 AAMI 结果、Youi 拖车责任和已标出的例外语义；如需下一轮生成，应显式从独立的新运行开始并记录所用代码版本。
共享来源 ID 报错可能被广播到多个引用路径的问题仍未在本轮处理；不能将路径条数当作独立错误数。本轮未 commit/push。
<a id="english-version-text"></a>

## English version

Subsequent user review and diagnostic-path fixes are recorded in [the post-review implementation report](car-insurance-post-review-20261006.md). The preceding sections retain this earlier stage's history.

This stage implemented cross-block exception checks, named-tier trailer-liability regressions, classified offline replay and read-only timeout lookup. No new generations, cancellations, historical response/schema/status edits, or holdout/test access occurred.

### 1. Exception continuations
In `src/car_insurance/source_coverage.py`, the old `following_blocks` selected the next non-navigation block. AAMI Hire incorrectly pointed to Incorrect fuel usage; citing that unrelated block alone could pass the exception check. A counterexample confirmed the checker defect.

A continuation now requires a parent ending in an exception-introducing colon, a compatible conditional bullet list, and adjacent pages. A parent already containing an inline list cannot borrow the next clause. New exclusions, unknown layouts and distant pages are not automatically linked. Confirmed forms include QBE `except when: → • it is / you have ...` and Youi `unless: → • you had ...`.

This is conservative structural matching, not a complete semantic continuation engine. Unsupported forms still require full introductory evidence and human review; fuzzy matching is not used. Tests cover the original counterexample, inline lists, unrelated exclusions, page distance and valid continuations.

### 2. Youi trailer liability
`trailer_liability_issues` in `src/car_insurance/extraction_diagnostics.py` requires a Legal Liability heading, named applicable products and a positive trailer/caravan towing-liability statement. Only explicitly named products are checked. Excluding damage to the trailer itself does not exclude third-party towing liability.

An explicit per-claim common cap requires reciprocal membership in the correct `per_claim/aggregate` pool, not QBE's `per_incident`. No insurer, fixed page or amount is hard-coded; unsupported wording/applicability is not guessed.

Replaying v5 Youi response 3 yields one `trailer_liability_missing` for each of three tiers. Its previous pass is no longer valid under current checks. Its 42 shared rules remain useful, but exclusion-checklist coverage does not prove coverage-category completeness.

### 3. Diagnostic categories
`classify_diagnostic` and `audit_saved_trials.py` distinguish:

- `structured_content_missing`: a dedicated field or explicit source relationship is missing.
- `evidence_issue`: incomplete/mismatched quotation or exception evidence, not automatically a semantic omission.
- `coverage_review`: insufficient source mapping requiring review.
- `structure_or_business_rule`: shape or cross-field constraints.

Checker defects require counterexample verification and are recorded separately. `agent_review.json` is targeted agent review, not human gold or user approval. AAMI TP Extra costs is missing structured exceptions; Hire already expresses its two exceptions but has incomplete evidence. The same evidence error must not conflate these cases.

The replay tool reads saved responses, validates shape before runtime checks, verifies input hashes and writes to a new directory. It replayed 13 saved v5 responses and three recovered responses.

### 4. Timeout recovery
Following the official Background mode guide linked above, `inspect_timeout.py` retrieves existing response IDs. It accepts only development records with TimeoutError and a response ID, uses read-only queries with a 30-second network timeout and no automatic retry. Credentials come from an explicitly selected env file or environment and are not printed. Original failure logs remain unchanged.

At 20:38 Sydney on 2026-10-06, all three requests were completed. Server elapsed time is completed_at minus created_at:

| Original request | Local timeout | Server duration | Recovered historical tokens | Offline result at that stage |
| --- | ---: | ---: | ---: | --- |
| v4r4 AAMI Comprehensive _rerun, request 2 | 900 s | 1,206 s | 118,141 | 0 diagnostics; human review pending then |
| v4r4 QBE TPPD _rerun, request 2 | 900 s | 1,584 s | 96,844 | 5 diagnostics |
| v5 AAMI Comprehensive, request 2 | 1,800 s | 2,501 s | 117,930 | 4 diagnostics |

These three were not permanently stuck: local waiting ended before completion. This does not explain server latency or earlier sleep interruptions without response IDs. No timeout increase, request change, resubmission or third repair was performed here.

Recovered historical usage totals 332,915 tokens, not new generation. v5 accounting becomes 1,456,955 + 117,930 = **1,574,885**; v5 AAMI Comprehensive becomes 101,238 + 117,930 = 219,168. These are API token counts, not currency bills. The original two-pass/two-fail/one-aborted record remains the historical local outcome, not a newly successful rerun.

### 5. Acceptance at that stage
Latest saved v5 responses: QBE TPPD 0 issues; Youi 3 trailer omissions; QBE Comprehensive 5 evidence diagnostics requiring semantic review; AAMI Third Party 2 diagnostics (Extra costs structured omission, Hire evidence); recovered AAMI Comprehensive request 2 has 4 diagnostics and no request 3.

Passing checks does not establish factual correctness or schema approval. Recovered v4 AAMI remains in a separate lookup directory, not silently promoted into the historical successful-output path.

### Artifacts, verification and next steps
The artifact paths and executable commands in the preceding bilingual document apply unchanged. `lookup_{1,2,3}.json` stores recovered outputs/status/usage; the verified audit report stores classifications and input/runtime hashes; `agent_review.json` records counterexample-backed checker defects and semantic spot checks.

All **217 selected offline tests passed** in 67.699 seconds, including nine new tests; model calls were mocked. Replay output directories must not already exist and need no new generation.

At this stage, priorities were human review of recovered v4 AAMI, Youi trailer liability and exception semantics. Any subsequent generation must use an independent run and recorded code version. Broadcasting shared source-ID errors to multiple paths remained unresolved at this stage, so path counts were not independent defect counts. No commit/push occurred in this historical implementation stage.
