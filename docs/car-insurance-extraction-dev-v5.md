# review_v5：保单级共享规则、省略号诊断、超时（2026-10-06，零调用）

[中文原文 / Chinese original](#chinese-original) · [English version / 英文版](#english-version-text)

<a id="chinese-original"></a>

后续更新：跨块例外误关联已修复，新增 Youi 拖车责任检查；三个超时请求经只读查询均已完成并找回输出。
v5 已知总用量补记为 1,574,885 tokens，Youi 原通过产物现在检出 3 个字段遗漏。下方保留当时运行记录；最新结论见 [v5 审核修复与结果找回](car-insurance-v5-audit-fixes.md)。

背景见 `car-insurance-extraction-dev-v4r4.md`：v4 要求多档位文档每个档位重复全部一般除外，Youi（67 条 × 3 档）输出过长，模型开始缩写、漏抄，两轮重跑都没收敛；AAMI Comp 卡在用 “...” 跳过 bullet 的例外证据；两轮 6 份运行中有 2 份因单次请求 900 秒超时中断。

本轮三项改动，全部零调用。代码提交 a082337；选定离线测试 **207 项通过**。

## 1. 省略号诊断（`evidence_ellipsis`，v4/v5 共用）

- 只检查**映射了清单 ID 的记录**（规则、benefit、excess）及其例外的证据。这些证据本来就要求逐字原文。
- 引用中出现 “...” 或 “…”，且该页原文没有这段文字时报错，路径指到具体 `evidence[i].quote`，并说明“把需要的句子/bullet 分成多条证据，不要用省略号跳过”。
- 普通证据（未映射清单 ID 的 benefit 等）允许缩写，不检查。最初做成全量检查时，原先通过的 QBE Comp、AAMI TP 也会多出 3–10 条，范围过宽，已收窄。
- GUIDANCE 同步加了一句。

零调用回放（全部已保存响应）：原先通过的 QBE Comp、AAMI TP 重跑仍为 0；AAMI Comp `_rerun2` 第 3 轮的 1 条问题现在会附带 4 条明确的省略号报错；Youi `_rerun2` 第 2 轮的 excess 缩写也被点名。

## 2. review_v5：保单级共享规则

新 profile `car_insurance.review_v5`（`src/car_insurance/schema_revision_v5.py`），在 v4 基础上：

| 改动 | 内容 |
| --- | --- |
| 顶层 `shared_policy_rules` | 与 `policy_rules` 同一 item schema；原文适用于整张保单 / 所有部分的规则（一般除外、一般条件）**只写一次**，单产品文档也放这里 |
| 产品字段 `shared_policy_rules_applicability` | `applies` / `does_not_apply`（需明确证据）/ null+_unfilled（未知） |
| 校验 | 共享规则 rule_id、文本不重复；不能引用产品的 benefit_id（用 `applies_to_events` 或文本表达范围）；共享规则的清单 ID 或文本不得在产品里重复出现；`applies` 时共享规则不能为空；例外检查与产品规则相同 |
| 来源检查 | 共享规则算作一个规则 owner，清单覆盖、完整引用、例外证据检查照常，报错路径为 `$.shared_policy_rules[i]...` |
| 全保单检查 | 原文有 “apply to all sections / not covered under any section” 时，每个产品“自有规则 + （applies 时的）共享规则”必须覆盖全部一般除外 ID；v5 下单产品文档也检查 |
| 提示词 | v4 中“每个档位重复一般除外”的两段替换为“写一次放在 shared_policy_rules，各产品标 applies” |

v4 不变，之前所有 v4 运行与回放结果不受影响。v4 产物不做原地迁移。

真实数据零调用验证：把 Youi 上一轮成功产物（规则都在 Comprehensive 上）转成 v5 形状——规则移到 `shared_policy_rules`、三个档位都标 `applies`——通过全部 v5 合约、校验和来源检查。一般除外从三份约 9.6 万字符降到一份约 3.2 万字符。
转换中发现 1 条（约 470 条规则中）共享规则引用了 Comprehensive 的 benefit_id；该规则已有 `applies_to_events`（theft / attempted_theft），所以保持“共享规则不得引用 benefit_id”，报错提示改用事件或文本。

## 3. 超时与运行方式

- 新运行脚本 `outputs/car_insurance/start_extraction_dev_v5.py`：单次请求超时 900 → **1800 秒**；`--run` 必须带 `--authorization`；runtime 哈希包含 `schema_revision_v5.py`。
- 建议 5 份**串行**运行，不再 3 份并行。
- schema 产物：`outputs/car_insurance/schema_trial_20261006_v5/`（schema、contract、provenance）。
- 已知限制：请求超时时没有响应，`token_usage.jsonl` 不会记录这次调用；如服务端已计费，不在统计里。

## 已准备、未运行

5 份开发文档的 v5 目录已准备（`extraction_dev_v5_20261006_<doc>/`：run_plan、schema 快照、清单、原文快照），清单数与 v4 相同：QBE TPPD 41、QBE Comp 44、AAMI Comp 32、AAMI TP 32、Youi 71。

运行需另行授权。按 v4 各文档用量估计，5 份串行最多约 160–180 万 tokens（每份最多 3 次调用），耗时约 2–3 小时。

## v5 运行结果（2026-10-06，用户确认 “可以”，5 份串行）

| 文档 | 结果 | 诊断轨迹 | Tokens（total） |
| --- | --- | --- | --- |
| QBE TPPD | **通过** | 14 → 2 → 0 | 271,281 |
| QBE Comprehensive | 失败 | 14 → 11 → 7（修复后回放 5） | 307,881 |
| AAMI Comprehensive | **中止（基础设施）** | 9 → — | 101,238（仅第 1 轮已记录） |
| AAMI Third Party | 失败 | 12 → 3 → 2 | 342,092 |
| Youi | **通过** | 19 → 7 → 0 | 434,463 |

合计已记录约 146 万 tokens；AAMI 第 2 次请求如已在服务端计费，不在此数中。

**通过的两份（内容抽查）**

- QBE TPPD：11 条共享规则（7 条带例外），产品标 applies、无自有规则。之前两轮漏掉的拖车责任这次有了，3,000 万责任池 3 个成员齐全。
- Youi：42 条共享规则覆盖全部 67 个清单项，三个档位都标 applies、无自有规则。一般除外只写一次（约 3.7 万字符）。v4 下两轮都没收敛的问题在 v5 下解决。
  待复核：三个档位的拖车责任都是 `[]`（已知没有），需对照原文确认；10 组例外全是 (source_defined, source_defined)，值得抽查。

**失败的原因**

- AAMI Comprehensive：第 2 次请求服务端 1800 秒仍 in_progress。放宽超时没有解决；AAMI Comp 的第 2 次请求已是第 3 次卡住（v4 `_rerun`、v5，另 QBE TPPD `_rerun` 一次），问题不在超时长短。
- AAMI Third Party：剩 2 条，都是真实遗漏，和 v4 首跑同类：Extra costs 只写了 travel/cleaning 两个例外，漏了 prior authority 和 “unless stated otherwise”；Hire 的例外引用从 “but we will provide cover…” 开始，缺句子前半部分。
- QBE Comprehensive：第 3 轮 7 条中 2 条是**检查器缺陷**——表格行清单项要求连 `|` 表格标记一起引用；v4 时模型照抄了标记所以通过，v5 时引用了干净正文反而失败。**已修复**：表格行的 `|` 标记视为版式，引用中可以省略，单元格文字仍须逐字。剩 5 条是真实遗漏：几条一般除外把多个原文块合并成一条规则但证据缺块，另有一条例外不全。

检查器修复后选定离线测试 **208 项通过**；全部已保存响应回放，原先通过的产物仍为 0。

## 尚未处理

- AAMI Third Party 的 5 组例外范围口径（见 v4r4 文档人工复核），检查器仍无法判断；可做 “(but not the driver” 类窄检查。
- 报错路径归属：某个清单 ID 只要有一个产品引用不全，报错会挂在所有引用它的路径上（含引用正确的产品）。

<a id="english-version-text"></a>

## English version

This is the historical review_v5 implementation/run record. Later timeout recovery raises known v5 usage to1,574,885 and identifies three Youi trailer-field omissions; see the audit-fixes report. Preserve earlier outcomes as historical, not current approval.

### Offline v5 changes
v4 repeated all exclusions per tier: Youi67×3 caused long output, abbreviations and omissions in two failed rounds. AAMI Comp skipped bullets with ellipses; two of six runs stopped at900s request timeout. Commit a082337 introduced three zero-call changes;207 selected tests passed.

evidence_ellipsis checks only checklist-mapped records and their exceptions, whose evidence must be verbatim. Unmapped ordinary benefit evidence is excluded. Unsupported .../… yields a precise evidence quote path and instruction to quote needed bullets separately. An initial all-evidence version wrongly added3–10 diagnostics to old passing outputs, so scope was narrowed. Replay preserves old QBE Comp/AAMI TP passes; AAMI _rerun2 final response gets four explicit ellipsis diagnostics beside its original issue, and Youi _rerun2 excess abbreviations are identified.

review_v5, built on v4, stores policy-wide exclusions/conditions once in shared_policy_rules, including single-product PDFs. Products use shared_policy_rules_applicability=applies/does_not_apply(with evidence)/null+_unfilled. Shared IDs/text cannot duplicate product rules or reference product benefit IDs; use event scope/text. applies requires nonempty shared rules. Evidence/checklist/exception checks treat shared rules as a document owner with proper paths. Explicit all-policy scope requires each product's own+applicable shared rules to cover the exclusion inventory. Prompts replace repeated per-tier instructions with shared storage.

v4 snapshots are not migrated. A memory-only conversion of the prior Youi output to v5 passed: shared exclusions reduce~96k→32k characters. One of~470 rules referenced a Comprehensive benefit ID, already covered by event scope; the invariant was retained, not relaxed.

start_extraction_dev_v5.py requires --run and --authorization, uses1800s instead of900s and hashes schema_revision_v5.py. Five serial runs were recommended. Schema/contract/provenance are in schema_trial_20261006_v5. Timeout calls without responses are absent from token_usage.jsonl until recovered.

### Preparation and original run
Five development plans/snapshots/checklists were prepared with counts QBE TP41, QBE Comp44, AAMI Comp32, AAMI TP32, Youi71. The historical estimate was1.6–1.8m tokens and2–3h, up tothree calls each, subject to separate authorisation.

User then approved five serial runs:
QBE TPPD passed14→2→0,271,281 tokens.
QBE Comp failed14→11→7(five after checker fix),307,881.
AAMI Comp aborted after9 diagnostics in attempt1,101,238 recorded.
AAMI TP failed12→3→2,342,092.
Youi passed19→7→0 at that time,434,463.
Original recorded total1,456,955 excluded the timed-out AAMI request.

QBE TP had11 shared rules/seven with exceptions, applies/no own rules, and all three AUD30m liability members. Youi had42 shared rules covering67 IDs, three applies/no own rules; previously repeated/abbreviated exclusions improved. Trailer fields [] and ten source_defined/source_defined exception groups required review.

AAMI Comp request2 remained in_progress after1800s; later recovery supersedes the original speculation about a stuck request. AAMI TP Extra costs omitted prior-authority/unless-otherwise-stated structure and Hire quoted only the latter introduction. QBE Comp had two checker defects requiring literal table bars around row text; these were fixed while retaining verbatim cell text. Five diagnostics remained on merged-block quotations/exception completeness. The original report called them omissions; later review distinguishes evidence failure from semantic loss. After checker repair208 selected tests passed; saved-response replay preserved earlier passes.

Open issues at this historical stage: semantic scope of five AAMI exception groups, potential narrow “but not the driver” checks, and errors broadcast across all records sharing an ID. Subsequent reports document later fixes.
