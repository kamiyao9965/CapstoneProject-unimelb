# v4 扩到其余 4 份开发文档：各跑一次（2026-10-06）

结论：**QBE Comprehensive、AAMI Comprehensive 通过；AAMI Third Party 失败；Youi 因基础设施问题中止，没有模型输出。**
都是 development 样本，各一次运行，候选 schema。通过校验不代表 schema 已批准，也不是人工 gold 或准确率。

## 运行

- openai/gpt-5，每份最多 3 次生成，4 份并行。运行前逐份断言：development 划分、PDF SHA、2026-10-03 parser review 指纹、schema 与构建器一致；冻结代码哈希和原文清单。
- 运行时为 v4 + r2 + 例外证据方案 A + 报错路径 + AAMI 检测 + 例外范围 / basis 口径（`run_plan.json` 中的 runtime_revision）。
- 未使用 holdout/test；QBE TPPD 历史目录未改写。

| 文档 | 页 | 清单 | 结果 | 诊断轨迹 | Tokens（total） |
| --- | --- | --- | --- | --- | --- |
| QBE Comprehensive 1123 | 48 | 44 | 通过 | 8 → 1 → 0 | 311,165 |
| AAMI Comprehensive 2020 | 76 | 32 | 通过 | 12 → 8 → 0 | 327,156 |
| AAMI Third Party 2020 | 64 | 32 | **失败** | 26 → 8 → 8 | 383,602 |
| Youi Car 2026 | 61 | 71 | **中止** | — | 未知（无响应） |

已记录的合计 1,021,923 tokens；Youi 首次调用如已在服务端计费，不在此数中。

输出：`outputs/car_insurance/extraction_dev_v4r4_20261006_<doc>/`（Git 忽略）。成功的两份产物在最新代码下离线复核仍通过。

## Youi：基础设施中止

首次调用后约 12 小时没有任何控制台输出，远超每次 900 秒的超时（同时启动的其他 3 份约 20 分钟内完成），已手动停止进程。
轮询逻辑每 30 秒应输出一次进度，但实际没有，说明卡在单个 HTTP 请求上。最可能是机器休眠导致连接失效。
没有取回任何响应，`run_status.json` 记为 `aborted_infrastructure`。这不是模型或检查的结果，Youi 尚未得到评估。

## AAMI Third Party：失败原因

第 3 轮剩 4 个问题（两个产品各报一次，共 8 条），第 2、3 轮完全相同：

| 规则（页） | 判断 |
| --- | --- |
| Confiscation（18）、Radioactivity（20） | **检查器缺陷**：解析器把印刷页码粘在块尾（“…its contents. 18”）。模型正确地没有引用页码，检查却要求逐字匹配。已修复。 |
| Extra costs（19） | **真实遗漏**：块内有 4 个例外，模型只抽了 travel/cleaning 两个，漏了 “prior authority” 和 “unless stated otherwise”。 |
| Unlicensed driving（22） | **未满足方案 A**：例外引用从 “but we will pay a claim…” 开始，缺少句子前半部分。 |

对照：AAMI Comprehensive 对**同样的两条**都做对了（4 个例外齐全，Unlicensed 引了整句）。说明方案 A 在这类措辞上可以达到，Third Party 的失败属于运行间波动，不需要放宽口径。

已修复的检查器缺陷（`src/car_insurance/source_coverage.py`）：

- 块尾跟在句末标点后的 1–3 位数字视为印刷页码，引用中可以省略；其他文字仍须逐字引用。
- 分句时不在 “e.g.” / “i.e.” 处断开，报错中的提示句因此完整。
- 新增 2 项回归，选定离线测试 **193 项通过**；QBE TPPD 原文清单不变。
- 用修复后的代码离线回放 AAMI Third Party 第 2、3 轮：8 → 4 条，只剩上表中后两项。

## 内容检查（通过的两份）

**QBE Comprehensive**（1 个产品，3 个附加选项：Hire Car Extra、No Excess Windscreen、Choice of Repairer）

- 11 条一般规则，7 条带例外；例外范围符合新口径（1 组 own/liability，3 组 both/none，5 组 source_defined）。
- 3,000 万责任共享池覆盖核心、替代车、拖车三项；4 项 excess 中 3 项有叠加规则，与 QBE TPPD 一致。
- 租车：未过错、盗窃后、可选附加三个变体，均为 reasonable_costs/per_day，盗窃后和附加选项保留 14 天。
- **待复核**：碰撞、风暴等自车损失事件没有挂任何额度或池。PDS 中的上限（投保金额/市场价）可能只写在 valuation_basis 字段里，需对照原文确认。

**AAMI Comprehensive**（1 个产品，3 个附加选项：Hire car unlimited days、Windscreen、Roadside Assist）

- 29 条一般规则，9 条带例外，与检测器的 9 个例外块对应；例外范围符合新口径。
- 事故后费用共享池（AUD 1,000，3 个成员）；5 项 excess 中 4 项有叠加规则。
- **待复核（R1 同类问题）**：AUD 20,000,000 责任上限分别写在核心责任、替代车、拖车三个 benefit 上，没有建共享池。
  原文（p.27）为 “all claims from any one incident for legal liability covered by this policy”，应是共用一个池。
  现有共享关系检查只识别 QBE 写法，所以没有拦住。是否扩展这项检查需另行决定。

## 小结

- 4 份中有 2 份在 3 次以内通过；修复轨迹与 QBE TPPD 类似（首轮问题多，第 2 轮基本修好）。
- 剩余问题有三类：真实例外遗漏（AAMI TP）、共享额度关系没有表达（AAMI Comp）、自车损失额度归属待确认（QBE Comp）。
- 每份只跑一次，单次结果有波动（AAMI 两份同样的条款一对一错），不能当作稳定性或准确率结论。

## 建议下一步

1. 重跑 Youi（这次没有得到任何结果）。启动前确认机器不会休眠，或给轮询/请求加上总时长保护。
2. 是否把共享责任池检查扩展到 AAMI 这类 “legal liability covered by this policy” 写法：需定规则，离线实现后回放 AAMI Comp。
3. 人工复核 QBE Comp 的自车损失额度表达，以及两份通过产物的抽样内容。
4. AAMI Third Party 可在检查器修复后再跑一次；仍不动 holdout/test。

## 后续：共享责任池检查扩展（2026-10-06，用户确认，零调用）

在 `src/car_insurance/extraction_diagnostics.py` 新增 `policy_liability_issues`：

- 触发条件：原文出现 “all claims from any one incident for legal liability **covered by this policy** is $N (million)”。
- “covered by this policy” 是全保单范围的上限，因此**多产品文档逐个产品检查**（此前的共享关系检查只用于单产品文档，QBE 写法仍保持原样）。
- 某产品有 2 个及以上受保责任项（核心责任、替代车责任、拖车责任）时，它们必须共用一个 per_incident/aggregate 池，金额等于原文（“$20 million” 解析为 20,000,000）。只有 1 个成员时不要求建池。原文中各处金额不一致时不检查，留给人工复核。
- GUIDANCE 同步增加一句说明。新增 2 项回归，选定离线测试 **195 项通过**。

零调用回放：

| 输入 | 新检查 |
| --- | --- |
| AAMI Comprehensive 成功产物 | **1 条**：2,000 万上限分别写在 3 个保障项上，没有建池（即上文的待复核项） |
| AAMI Third Party 第 3 轮 | 0：两个产品都正确共用了 2,000 万池（已核实正则确实匹配了原文 p.25、p.34） |
| QBE Comprehensive、QBE TPPD（r3 两次）、v3 | 0：QBE 写法不触发新规则，结果不变 |

AAMI Comprehensive 的成功产物因此**不再符合当前检查**，需要重跑或人工修订后才能当参考数据。

## 后续：Youi 与 AAMI Third Party 重跑（2026-10-06）

用户确认 Youi 中止是电脑休眠所致，同意重跑；并同意检查器修复后重跑 AAMI Third Party。
运行脚本加了 `--tag`（写入独立的 `_rerun` 目录，原失败/中止目录不改）。Windows 上运行期间阻止系统休眠，只作用于运行脚本本身，不改抽取代码。
两次运行使用的代码包含页码 / e.g. 修复和全保单责任池检查。

| 文档 | 结果 | 诊断轨迹 | Tokens（total） |
| --- | --- | --- | --- |
| AAMI Third Party（重跑） | 通过 | 18 → 16 → 0 | 376,950 |
| Youi Car 2026（重跑） | 通过 | 26 → 3 → 0 | 440,359 |

**AAMI Third Party**：2 个产品（Fire, Theft & TPPD；TPPD），每个 30 条一般规则、9 条带例外，各自都有 2,000 万共享责任池。
上次失败的两项这次都对了：“Extra costs” 的 4 个例外齐全，“Unlicensed driving” 引用了整句。

- 待复核：Unlicensed 例外填了 (both, none)，但原文是 “we will pay a claim for you (but not the driver…)”，除外并没有被完全解除；按口径应为 (source_defined, source_defined)。检查器无法判断这种语义区别。
- 两个产品的全部例外都是 both/none（共 13 组），值得抽查是否都合理。

**Youi**：3 个档位（Comprehensive、Third Party Fire & Theft、Third Party Property Only），各有 2,000 万共享责任池（原文 “$20,000,000 … each claim”）。

- **内容错误**：一般除外只写在 Comprehensive（28 条）上，另外两个档位的 policy_rules 是 `[]`（表示“已知没有”）。原文明确写着 “These general exclusions apply to all sections of your policy.”，所以这两个档位被错误地表示为没有一般除外。
- 检查器只要求每个清单 ID 出现在至少一个产品里，所以没有拦住。这是已记录的限制（“不证明每个档位都正确应用了全局条款”）。

## 汇总：5 份开发文档当前状态

| 文档 | 最新运行 | 当前检查下的产物 | 主要待处理 |
| --- | --- | --- | --- |
| QBE TPPD | r3 两次通过 | 不符（例外范围 / basis 口径收紧后） | 按新口径重跑或人工修订 |
| QBE Comprehensive | 通过 | 符合 | 自车损失额度归属待核实 |
| AAMI Comprehensive | 通过 | 不符（责任池检查扩展后） | 2,000 万上限未建池 |
| AAMI Third Party | 重跑通过 | 符合 | Unlicensed 例外范围口径 |
| Youi | 重跑通过 | 不符（全保单一般除外检查新增后） | **两档缺一般除外** |

本轮合计（4 份首跑 + 2 份重跑，不含 Youi 中止那次）约 184 万 tokens。

## 后续：一般除外全保单适用检查（2026-10-06，用户确认，零调用）

在 `src/car_insurance/extraction_diagnostics.py` 新增 `policy_wide_rule_issues`，由 `validate_runtime` 调用：

- 触发条件：多产品文档，且原文出现 “apply to all sections of your/this policy” 或 “no cover / not covered under any section of this/your policy”。
- 满足条件时，**每个产品**的 `policy_rules` 都必须映射清单中全部一般除外 ID；报错给出该产品已映射数 / 总数和部分缺失 ID。证据与例外的完整性仍由原有逐条检查负责。
- 单产品文档不触发（原有清单覆盖检查已要求全部 ID）；没有上述措辞时不推断适用范围。
- GUIDANCE 同步增加说明。新增 2 项回归，选定离线测试 **197 项通过**。

零调用回放（当前代码，全部已保存响应；各目录清单无漂移）：

| 输入 | 新检查 |
| --- | --- |
| Youi 重跑第 3 轮（原成功产物） | **2 条**：Third Party Fire & Theft、Third Party Property Only 均为 0/67 |
| AAMI Third Party 重跑第 3 轮 | 0：两个产品都映射了全部 29 个 ID（原文 p.18 “not covered under any section of this policy”） |
| AAMI Third Party 首跑第 2、3 轮 | 0（仍有原来的 4 条其他问题） |
| QBE Comprehensive、AAMI Comprehensive、QBE TPPD（v4–r3） | 0：单产品，结果与之前相同 |

Youi 的成功产物因此**不再符合当前检查**。
成本预估：Youi 每个档位的一般除外约 3.2 万字符（约 8K output tokens），补齐两个档位每轮约多 1.6 万 output tokens；上次重跑 44 万 tokens，预计下次约 50–55 万。

注意：`compare_extraction_dev_v4r4.py` 会断言运行时代码哈希与 `run_plan.json` 一致，代码改动后对已有目录会报 drift，这是预期行为；本节回放用的是不检查哈希的临时脚本。

## 留存

各目录：`run_plan.json`、`schema_snapshot.json`、`source_checklist.json`、`source_representation.md`、`extraction_contract.json`、`request_*.json`、`response_*.json`、`run_status.json`、`token_usage.jsonl`、`run_console.log`、`comparison.json`（失败/中止的目录没有产物）。
脚本：`outputs/car_insurance/start_extraction_dev_v4r4.py --doc <name>`；对比：`compare_extraction_dev_v4r4.py <run_dir>`（零调用）。
