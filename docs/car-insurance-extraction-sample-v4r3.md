# v4 + r2 + 例外证据方案 A：同样本两次独立复跑（2026-10-06）

[中文原文 / Chinese original](#chinese-original) · [English version / 英文版](#english-version-text)

<a id="chinese-original"></a>

结论：**两次都通过完整运行时校验，都生成了成功产物；修复路径一致（10 → 1 → 0 条诊断）。**
这是该开发样本首次在 v4 下通过。S1–S5、R1/R2 两个共享池以及此前修不动的例外问题，两次结果一致且正确。
通过校验不等于 schema 已批准，也不是人工 gold 或准确率；本次发现 1 处语义字段错误（见下）。只有 n=2，属于单文档的初步稳定性证据。

## 运行

- 输入与 v3/v4/r2 完全相同（PDF、解析指纹、模型原文、41 项清单在运行前断言一致）；development 样本，未使用 holdout/test。
- schema 仍为 `schema_review_v4.json`（候选稿）。与 r2 的唯一区别是例外证据方案 A 及相应诊断/GUIDANCE，代码哈希见各自 `run_plan.json`。
- openai/gpt-5；两次并行、互相独立（不同 response_id，最终 JSON 不同）。每次均 3 次生成，仍受 3 次上限约束。

| | run1 | run2 |
| --- | --- | --- |
| 结果 | success | success |
| 诊断轨迹 | 10 → 1 → 0 | 10 → 1 → 0 |
| 抽取计时 | 506.6 秒 | 507.4 秒 |
| Tokens（input / output / total） | 210,449 / 59,367 / 269,816 | 209,043 / 60,334 / 269,377 |

输出：`outputs/car_insurance/extraction_sample_v4r3_20261006_qbe_tppd_run{1,2}/qbe_tppd_1123.json`（Git 忽略）。
最终产物与 `response_3.json` 一致；`comparison.json` 离线复跑完整检查同样通过。

## 修复轨迹

- **第 1 轮**（两次相似）：ID 重复、例外相关 4 条、证据不匹配 3 条，以及共享池 2 条（run1）或缺失映射 2 条（run2）。
- **第 2 轮**：上述问题全部修好，包括轮胎/机械故障例外和完整条目引用；两次各只剩 1 条 `missingness: _unfilled/null mismatch`。
- **第 3 轮**：通过。

与 r2 对比：同类例外问题在 r2 中两轮修复都没动。方案 A 的报错直接给出需引用的原句后，两次都在一轮修复内修好。

## 内容检查（两次最终产物）

| 项目 | 结果 |
| --- | --- |
| 产品边界 | 1 个 TPPD 产品 + 1 个 Fire & theft 附加选项；未生成 Comprehensive 产品（run2 在 notes 中说明了误导性标题） |
| S1 司机例外 | 两次：own_vehicle_damage 保留，third_party_liability 不恢复 |
| S2 全局规则 | 两次均 11 条，41 项清单全部映射；轮胎、机械故障各有一条独立例外；“承认责任”和“车况”例外引用完整条目；拼车/网约车例外引用下一块 |
| S3 租车日费用 | reasonable_costs/per_day；租期 lte 14 days；挂在 Fire & theft 选项下（optional） |
| S4 换车保障 | 独立 change_of_vehicle_cover，lte 14 days |
| S5 excess 叠加 | 三项 combination_rule 均已填写（run1 为短代码，run2 为句子；含义相同） |
| R1 共享责任池 | 两次：核心、替代车、拖车三者互链一个 per_incident/aggregate AUD 30,000,000 池 |
| R2 Fire & theft 共同赔付 | 两次：fire/theft/attempted theft 共用 lesser_of(schedule_specific, market_value) 池 |
| R3 无保险司机 | 两次均为 lesser_of(5000, market_value)，与 v3 相同；仍待对照 PDF 11、20 人工复核 |
| 元数据 | 准备日期 31 July 2023，effective_date null |

## 发现的问题与两次之间的差异

1. **语义字段错误（run1）**：`rule_precautions` 例外的 `condition` 写成了被除外的行为（“承认过错/责任”），而不是例外条件（“除非本应予以承保”）。`effect` 字段是对的；run2 正确。检查器只验证引用与结构，**不验证 condition 的语义**。
2. **池的 basis 不一致**：Fire & theft 池 run1 为 aggregate，run2 为 per_vehicle（r2 和 v3 也是 per_vehicle）。需要人工确定规范取值。
3. **例外范围字段口径不一**：如“车况”例外，run1 为 both/none，run2 为 source_defined/source_defined。不算错，但说明 preserved/not_restored 缺少统一口径。
4. `_unfilled/null mismatch` 报错没有字段路径，两次都多花一轮修复，约 9 万 tokens。这是下一个值得离线改进的点。

## 结论边界

- 回答“真实模型能否稳定修正”：在这一个开发文档上，2/2 次在上限内修到通过，内容关键项一致。比 r2（0/1）和 v4（0/1）明显改善。
- 样本量小、单一文档，不能推广到其他保险公司/版式，也不能当作 benchmark。
- 最终产物仍属候选 schema 下的抽取结果，需要人工复核（尤其是上述 1–3）后才能当参考数据。

## 建议下一步

1. 离线：让 `_unfilled/null mismatch` 报错带路径；考虑对例外的 condition 增加轻量一致性检查，或至少列入人工复核清单。
2. 人工复核本次两个产物的差异项（池 basis、例外 scope 口径），把结论写进 schema 说明。
3. 之后再扩到其他开发文档（AAMI、Youi 等）。注意 AAMI 的一般除外章节目前不在检测器覆盖范围内。仍不动 holdout/test。

## 后续：离线改进（2026-10-06，零调用）

**报错可定位**（`src/car_insurance/schema_revision_v3.py`，v3/v4 共用，前缀不变）：

- `missingness` 报错现在包含产品路径，以及“null 但未列入 _unfilled”和“列入但非 null”的字段名。
  两次复跑第 2 轮的唯一失败分别是 3 个、2 个字段没列入 `_unfilled`，旧报错没有给出这些字段名。
- `benefit_uniqueness` 报错现在包含冲突的 benefit_id 与 (category, variant, option_id)。
  两次复跑第 1 轮都是 fire/theft/attempted theft 三个事件用了同一个 variant，新报错会直接点名。

**AAMI 一般除外检测**（`src/car_insurance/source_coverage.py`）：

- 识别带编号的 “3. Things we don't cover / what we do not cover” 章节（按规范化文本匹配，弯引号同样识别）；仍要求后续内容含除外措辞，所以目录不会被当成章节。
- 编号章节只在遇到**更大编号**的标题时结束，避免 Youi 一类章节内的 “1. … 2. …” 条目把章节提前截断。
- 同一章节内逐字重复的引导句（AAMI 每页页首、Youi 的 “(cont.)”）只计第一次。
- 例外提示新增 “but we will/we'll provide cover / pay / cover”（AAMI 写法）。

开发集检测结果（`outputs/car_insurance/detector_coverage_20261006.json`）：

| 文档 | 之前 policy_rule | 现在 policy_rule / 含例外 |
| --- | --- | --- |
| AAMI comprehensive 2020 | 0 | 29 / 9 |
| AAMI third party 2020 | 0 | 29 / 9 |
| QBE comprehensive 1123 | 36 | 36 / 7（不变） |
| QBE TPPD 1123 | 36 | 36 / 7（逐项不变，已断言） |
| Youi car 2026 | 69 | 67 / 9（去掉 2 条 “(cont.)” 重复页首） |

两次复跑的 QBE 最终产物在新代码下离线复核仍通过。新增 4 项回归，选定离线测试 **188 项通过**。
5 份开发 PDF 都在 2026-10-03 的 parser review 指纹范围内。检测结果只说明检测器的覆盖面，不是 gold 标注。

## 后续：取值口径（2026-10-06，用户确认，零调用）

用户确认了“发现的问题”第 2、3 项的口径，已写入运行时 GUIDANCE，并在 `src/car_insurance/extraction_diagnostics.py` 中按路径检查：

1. **limit basis**：凡是含 `market_value` 项的额度（即按投保车辆价值封顶）一律 `basis=per_vehicle`。
   用户确认的是 Fire & theft 池；同样按车辆市值封顶的无保险司机额度 lesser_of(5000, market_value) 也一并适用，两次复跑在这里也不一致。
   固定金额的责任池（AUD 30m）仍为 aggregate。租车、拖车的 basis 未定口径，不检查。
2. **例外范围**：(preserved_cover, not_restored_cover) 只允许三种组合：
   - (own_vehicle_damage, third_party_liability)：仅当例外证据明确区分了自车损失与责任（证据中须出现 liability）；
   - (both, none)：例外让整条除外不再适用；
   - (source_defined, source_defined)：其他情况。不再使用 unknown。

`schema_review_v4.json` 候选稿文件保持不变（历史试跑绑定其哈希）；口径属于运行时修订。以后正式修订 schema 时应写入字段说明。

在新口径下复核两次复跑的 QBE 产物：run1 有 7 条（5 条例外组合、2 条 basis），run2 有 2 条（1 条例外组合、1 条 basis）。
这说明口径收紧了，不代表当时的抽取变差；**这两个产物不再符合当前检查**，如需作为参考数据，应在新口径下重跑或人工修订。
新增 3 项回归，选定离线测试 **191 项通过**。

## 留存

每个 run 目录包含：`run_plan.json`、`schema_snapshot.json`、`source_checklist.json`、`source_representation.md`、`extraction_contract.json`、`request_1/2/3.json`、`response_1/2/3.json`、`run_status.json`、`token_usage.jsonl`、`run_console.log`、`qbe_tppd_1123.json`、`comparison.json`、`content_spot_check.json`。
运行脚本：`outputs/car_insurance/start_extraction_sample_v4_r3.py --trial {1,2}`；对比：`compare_extraction_sample_v4r2.py <run_dir>`（零调用）。

<a id="english-version-text"></a>

## English version

Two independent same-document runs on2026-10-06 passed full v4+r2+convention-A checks, both10→1→0, the first v4 acceptance for this sample. Key S1–S5/shared-pool/exception items agreed, but one semantic field error remained. n=2 is preliminary single-document evidence, not approval, gold or accuracy.

Inputs/PDF/parser/source/41-item inventory matched prior trials; development only. Candidate schema_review_v4 is unchanged; convention A/diagnostics/GUIDANCE are the difference from r2, hashes recorded. Two parallel independent gpt-5 runs have distinct response IDs/JSONs, three calls each. Run1:506.6s, input210,449/output59,367/total269,816; run2:507.4s,209,043/60,334/269,377. Files are extraction_sample_v4r3_20261006_qbe_tppd_run{1,2}/qbe_tppd_1123.json, matching raw response3; offline comparison also passed.

Initial issues: duplicate IDs, four exception diagnostics, three evidence mismatches, plus two pool issues(run1) or missing mappings(run2). One repair fixed these including tyre/mechanical exceptions and whole-item quotations; each then had one missingness error. Attempt3 passed. Unlike r2's stalled repairs, exact source text in diagnostics enabled both to repair these exceptions in one step.

Content: one TPPD+Fire & Theft add-on, no Comprehensive (run2 notes the misleading title); own-damage-only driver exception;11 rules mapping all41 IDs, separate tyre/mechanical exceptions, full precautions/car-condition items and next-block carpool/rideshare evidence; optional reasonable_costs/per_day hire up to14days; distinct vehicle-change up to14days; three stacking rules(short codes versus prose); reciprocal core/substitute/trailer AUD30m per_incident/aggregate pool; shared fire/theft/attempted-theft lesser_of(schedule_specific,market_value); uninsured lesser_of(5000,market_value) still needs pages11/20 review; preparation31 July2023/effective null.

Run1 rule_precautions.condition incorrectly describes admitting fault, not the unless-covered-anyway exception; effect is correct, run2 is correct. Checks validate citations/structure, not condition semantics. Fire/theft pool basis differs aggregate versus per_vehicle; car-condition scope differs both/none versus source_defined/source_defined. Missingness errors lacked field paths, costing another repair(~90k tokens each run's order of magnitude). Review these discrepancies before reference use; do not generalise two passes to other insurers/layouts. Proposed next steps were precise errors, semantic spot checks, scope/basis decisions, then other development PDFs, not holdout/test.

### Subsequent offline changes
schema_revision_v3.py now reports product path and fields null-but-not-_unfilled or vice versa; the two second attempts omitted three/two fields. benefit_uniqueness now names ID and category/variant/option tuple; initial runs reused the same variant for all three fire/theft events.

source_coverage.py recognises numbered Things we don't cover/what we do not cover headings, including curly quotes, with exclusion wording to avoid contents matches. Only a larger heading number closes a numbered section, preserving internal enumerations. Repeated identical section introductions/(cont.) are counted once. AAMI but-we-will provide-cover/pay/cover cues are recognised.

Detector counts: AAMI Comp0→29 rules/9 with exceptions; AAMI TP0→29/9; QBE Comp36/7 and QBE TPPD36/7 unchanged; Youi69→67/9 after removing two repeated headers. QBE outputs still passed at this stage; four new regressions,188 selected passes. All five development inputs belong to the2026-10-03 parser review. Counts describe detector coverage, not gold.

User-confirmed conventions were then added to GUIDANCE/path checks: any market_value term implies per_vehicle, including uninsured-driver limits; fixed AUD30m liability stays aggregate; hire/towing basis not decided. Allowed preserved/not-restored pairs: own_vehicle_damage/third_party_liability only with explicit liability distinction; both/none for lifting the whole exclusion; otherwise source_defined/source_defined, not unknown.

Historical candidate JSON/hash remains unchanged; a future schema revision should put conventions in descriptions. Under stricter checks run1 has7 issues(five scope,two basis), run2 two(one each). This is tightened policy, not deterioration at generation time; neither qualifies under current checks without rerun/manual revision. Three more regressions,191 passes.

Preserved artifacts and exact scripts/commands are listed above: both full raw runs, status/usage/source/schema/contracts, comparison and agent spot checks. Replays make no model calls.
