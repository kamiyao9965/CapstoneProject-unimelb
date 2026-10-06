# v4 + 诊断 r2 同样本复跑：QBE TPPD（2026-10-05）

结论：**仍未通过验收，没有成功产品产物；但失败形态明显收敛。**
r2 的修复上下文让模型稳定保住了已修正的内容（表格、共享额度池、S1/S3/S4/S5），未再出现 v4 那种“修一处坏一处”。
剩下 5 条诊断在第 2、3 轮**原样不变**：模型在最后一轮修复中实际只改了两个无关字段，修复已停滞。
单次运行不能证明“稳定”；本次只能说明：r2 解决了回退问题，但例外证据这一类错误靠当前提示/诊断修不动。

## 运行与复验

- 输入与 v3/v4 完全相同：`data/car_insurance/development/qbe/pds/qbe_tppd_1123.pdf`（40 页，development）；PDF SHA、解析指纹、模型原文、41 项原文清单均在运行前断言一致。
- schema：`schema_trial_20261005/schema_review_v4.json`（候选稿，未批准，与 v4 试跑相同）。变量是 r2 运行时（诊断、提示、修复上下文），运行代码哈希记录在 `run_plan.json`。
- 模型 openai/gpt-5；3 次生成（初始 + 2 次修复）后达到上限停止，没有额外调用。
- 开始 2026-10-05 09:55:13 UTC，结束 10:06:36 UTC；抽取计时 681.982 秒。
- Tokens：input **207,664**；output **61,491**；total **269,155**（v4 为 262,422）。修复轮输入约 73.5k，比首轮 60.5k 多约 13k，即带入上一轮候选的成本；输出从约 25k 降到约 18k。
- 未使用 holdout/test；旧 v3/v4 目录未改写。

输出目录：`outputs/car_insurance/extraction_sample_v4r2_20261005_qbe_tppd/`（Git 忽略）。

## 每轮诊断（r2 完整检查，离线复算与运行时一致）

| 轮次 | 诊断 | 变化 |
| --- | --- | --- |
| 1 | 10 条：benefit_uniqueness 1、缺失来源映射 2（PDF 14）、共享责任池 1、Fire & Theft 共同赔付池 1、例外相关 5 | 表格列数首轮即正确（v4 三轮中两轮失败于此） |
| 2 | 5 条：只剩例外相关 | 5 类问题一次修好，包括 R1/R2 两个共享池；没有引入新错 |
| 3 | 5 条：与第 2 轮逐字相同 | 全文仅 2 处差异：`uninsured_driver.limits[0].basis`，以及 excess combination_rule 中模型自造的大小写错误 `APPlicable` |

这些是诊断条数，不是准确率或人工 gold 分数。

## 剩余 5 条：哪些是模型问题，哪些是检查约定问题

| 规则（PDF 页） | 模型输出 | 判断 |
| --- | --- | --- |
| `rule_other_loss_damage`（16）：轮胎损坏 / 机械故障 “unless it's caused in an incident for which we've agreed to pay a claim” | 三轮均**没有** exceptions（报 2 条） | **真实遗漏**。路径与 rule_id 都已明确给出，模型两轮修复都没有补上。v4 旧试跑第 3 轮曾补出，说明不稳定 |
| `rule_reasonable_precautions`（15）：“…admits fault…, unless we would have provided cover under your policy anyway.” | 有例外，条件正确；例外 evidence 只引了 “unless …” 片段，完整块在父规则 evidence 中 | 语义正确，**未满足证据约定**：检查要求完整块出现在 exceptions[].evidence |
| `rule_condition_of_car`（17）：“…unroadworthy…, unless its condition did not cause or contribute to the incident.” | 同上，片段引用 | 同上 |
| `rule_use_of_car`（15）：“to carry passengers for hire… except when:” + 下一块列出拼车/网约车条件 | 有例外，条件正确；evidence 引的是**下一块**（真正的例外内容） | 语义正确，且引用更贴切；但检查要求的是带 “except when:” 提示的那一块。例外跨块时，当前约定会迫使模型引用无关的“送餐”条目 |

修不动的原因（基于 request_2/3 实际内容）：

1. 诊断 `exclusion exception needs explicit exceptions[] with full source evidence` 只给 source_id，**没有说明“已有例外，但其 evidence 缺少下列完整原文”**，也没给出需引用的原文。模型看到“已有 exceptions”，就认为已满足。
2. 提示（GUIDANCE）同时写了“完整证据可在父规则或其 exceptions 上”与“每个例外仍需完整相关原文块”。两条在模型看来有冲突，模型选择了前者。
3. 轮胎/机械故障例外是纯模型遗漏；在明确路径提示下仍未补，说明仅靠反馈不一定修得好。

## S1–S5 与 R1–R3（以第 3 轮未通过候选为准，对照 v3 / v4 旧试跑）

| 项目 | 结果 |
| --- | --- |
| S1 司机除外例外（14） | 三轮稳定：own_vehicle_damage 保留，third_party_liability 不恢复 |
| S2 全局规则（14–17） | 11 条，其中 6 条有 exceptions；第 2 轮补齐 PDF 14 两个缺失映射。轮胎/机械例外仍缺（见上） |
| S3 租车合理日费用（13） | 三轮稳定：reasonable_costs/per_day，无编造金额 |
| S4 换车保障（11） | 三轮稳定：独立 change_of_vehicle_cover，lte 14 days；本次未再报证据不匹配 |
| S5 excess 叠加（26） | 三项均填 combination_rule；第 3 轮一项出现 `APPlicable` 大小写错误（语义不变，但属于无意义改动） |
| R1 共享责任池 | **已恢复**（第 2 起）：tppd_core、substitute car、trailer 三者互链一个 per_incident/aggregate AUD 30,000,000 池，成员不再重复存额度 |
| R2 Fire & Theft 共同赔付 | **已恢复**（第 2 起）：fire/theft/attempted theft 共用 lesser_of(schedule_specific, market_value) 池，attempted theft 不再缺 total-loss 分支 |
| R3 无保险司机额度 | 回到 v3 的统一 lesser_of(5000, market_value)，并写明 repair/total loss 条件；仍需按 PDF 11、20 人工复核 |

保留项：1 个 TPPD 产品 + 1 个 Fire & theft 附加选项；PDS 准备日期 31 July 2023；effective_date null。
v3 成功记录对新共享关系检查仍为 0 问题。

## 对“能否稳定修正”的回答

- **能稳定保持**：r2 带入上一轮候选和错误历史之后，已修好的字段在后续轮次中全部保住，没有复发（v4 的主要失败模式）。
- **能修正**：路径明确、要求具体的错误（共享池、缺失映射、ID 唯一性）在一轮修复内全部修好。
- **不能修正**：例外证据放置（4 条）和一处真实例外遗漏（1 条）；两次修复后完全不变。
- 只有一次运行，没有统计意义；不同运行的首轮差异（v4 首轮失败于表格，本次首轮表格正确）说明首轮输出本身有随机性。

## 建议的下一步（均为离线，未实施）

需先决定例外证据的约定，再改代码：

1. **方案 A（推荐）**：父规则 evidence 已有完整原文块时，例外 evidence 只需引用该块中逐字存在的片段，且须覆盖 unless/except 提示的完整句子；例外跨块时允许引用后续相邻块。这仍是严格的逐字匹配，不是模糊匹配。
2. **方案 B**：保留现行严格约定，但诊断改为“`policy_rules[i].exceptions[j].evidence` 缺少以下完整原文：<原文>”，并去掉 GUIDANCE 中的冲突表述。
3. 无论选哪种：对“已有映射、缺例外”的报错附上触发的原文句子（如轮胎/机械那句），帮助定位真实遗漏。
4. 先用本次 3 个 response 做零调用回放，验证新约定下只剩真实遗漏，再决定是否付费复跑。若要评估稳定性，需同样本重复 2–3 次。

## 后续：方案 A 已实施（离线，零调用）

用户确认采用方案 A。修改 `src/car_insurance/source_coverage.py`、`src/car_insurance/extraction_diagnostics.py`：

- 父规则仍须逐字引用完整原文块（未放宽）。
- 例外的 evidence 只需在同页逐字引用含 unless/except/does not apply 提示的**完整句子或条目**；只引 “unless …” 半句仍不通过。
- 提示句以 “:” 结尾且例外内容在下一块时，引用**完整的**下一相邻块（同页或下一页）也可；未提供原文时此项不放行。
- 同一块有多个提示句时，每个都需有例外覆盖。部分引用不得掺入原文没有的文字。
- 报错直接给出需引用的原句及已有例外数；`scoped_exceptions` 报错附上触发提示的原句。
- GUIDANCE 中“每个例外须引完整原文块”的冲突表述已改为上述约定。旧做法（例外引完整块）依然通过。

新增 7 项回归；选定离线测试 **185 项通过**。

零调用回放（`outputs/car_insurance/exception_convention_a_replay_20261005_{v4r2,v4}/replay_report.json`）：

| 保存的输入 | 原 r2 检查 | 方案 A |
| --- | --- | --- |
| r2 response_1 / 2 / 3 | 10 / 5 / 5 | 9 / 4 / 4 |
| v4 旧 response_1 / 2 / 3 | 12 / 52 / 13 | 10 / 52 / 11 |
| v3 共享关系 | 0 | 0 |

r2 第 3 轮剩下 4 条，都不是检查约定误报：

- `rule_use_of_car`（跨块例外）**不再报错**。
- `rule_other_loss_damage`：轮胎/机械故障例外**真实遗漏**（2 条）。
- `rule_reasonable_precautions`、`rule_condition_of_car`：例外只引了 “unless …” 半句，缺少说明它修饰哪一条的前半部分。报错现在给出需引用的完整条目原文。

以上只说明诊断更准确、更可操作，不代表模型下次能修好。是否付费复跑，以及是否连续多次复跑评估稳定性，另行决定。

## 留存

- `run_plan.json`、`schema_snapshot.json`、`source_checklist.json`、`source_representation.md`、`extraction_contract.json`
- `request_1/2/3.json`、`response_1/2/3.json`（原始，未人工改写）
- `run_status.json`、`token_usage.jsonl`、`errors/`、`run_console.log`
- `comparison.json`：离线复算的每轮完整诊断与内容索引（`compare_extraction_sample_v4r2.py` 生成，零调用）
- `content_spot_check.json`：本次定向检查结论，代理审查，不是人工 gold
