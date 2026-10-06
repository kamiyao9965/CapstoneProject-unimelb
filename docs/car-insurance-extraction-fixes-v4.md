# 抽样问题修复 v4（2026-10-05）

状态：已实现新的候选 schema、原文完整性检查和回归测试。**没有改写 v3 模型结果，没有宣称真实模型已消除全部遗漏。**
实现阶段仅做离线验证。2026-10-05 用户确认后已重跑同一 QBE 开发样本：3 次生成后仍未通过校验，内容有改善但共享额度关系回退，未使用 holdout/test 文档。
实际运行结论见 [v4 同样本重跑报告](car-insurance-extraction-sample-v4.md)；以下离线测试结果不能替代该真实失败记录。

## 五个问题的处理

| 发现 | 修改 | 能检查的内容 |
| --- | --- | --- |
| S1 除外漏掉例外 | policy_rules 增加 exceptions（条件、效果、恢复/未恢复保障范围、证据） | 有例外提示的原文必须关联显式例外；例外与前面的主体规则放在同一记录；自身损失恢复但第三方责任不恢复时不能抹平范围 |
| S2 一般规则漏抽 | 从实际输入建立原文块清单，规则/保障/excess 用 source_clause_ids 逐项对应 | 清单项遗漏、未知/过期 ID、错字段、错页、只引用部分原文都会失败，并进入已有有限修复流程 |
| S3 合理日费用漏填 | 在提示和业务校验中同时检查 reasonable daily cost | hire_car_benefits 必须有 reasonable_costs/per_day 的限额项，不能只写到 evidence；不凭空填固定金额 |
| S4 换车保障漏抽 | 新增 change_of_vehicle_cover 类别和字段 | 与维修期间临时替代车、租车期限分开；用 time_since_vehicle_change 数量，原文明确 up to N days/hours/months 时保留上限 |
| S5 excess 叠加漏填 | 对原文/证据中 in addition to ... excess 作一致性检查 | combination_rule 不能为 null/空字符串；仍不把未知金额相加 |

新增 v4 是因为输出结构发生变化。旧 v2/v3 schema、验证行为和原始抽样结果保留，不能简单把旧结果的版本号改为 v4。
v4 仍为候选稿，不构成人工审批，也没有自动设成所有调用的默认 schema。

## 实际抽取路径

SchemaExtractor 在 v4 下执行：

1. 使用已有解析质量门槛，取得带物理页码和 block/table ID 的输入。
2. 建立保守的原文清单，连同输入一起发给模型；不为发现条款另加一次模型调用。
3. 先做 JSON Schema 和 v3 基础业务规则校验，再检查 v4 例外/专用字段规则。
4. 对照本次输入检查 source_clause_ids、完整引用、字段归属和例外挂接。
5. 未通过则进入已有最多两次结构/业务修复；仍失败就记失败，不输出伪成功的产品文件。

没有页码/block 锚点的 v4 输入会在模型调用前停止。每次 extract_one 都重新建立清单，避免把上一份 PDF 的清单带到下一份。
同一个已检测条款需要至少在文档中的一个适用产品中得到解释；这不自动证明每个档位都正确应用了全局条款，仍需人工核对。

## 为什么不是只给 QBE 填答案

实现不包含 QBE 名称、PDF 文件名、固定页码、固定金额或固定 14/25 等数字。
检查依据是当前输入的章节、文本块、条款提示及 ID/内容指纹。
测试包括不同页码、不同时间数字、跨页规则、目录/页脚负例、合理拖车费用不误当租车日额等。

在保留的 QBE 开发输入中，这些检测产生 41 个块：36 个 policy_rule、1 个 hire_daily_cost、1 个 change_of_vehicle、3 个 excess_stacking。
原文清单是回归的辅助依据，不是 41 条人工 gold 标签。旧 v3 没有 source_clause_ids，因此不能把“41 个缺少 ID”当成“41 个语义错误”或计算准确率。
旧结果中的租车日费用漏填和三项 excess 叠加漏填，可不依赖这些新 ID 单独检出。

另外对 5 份开发集的已解析内容做了离线识别检查：QBE 两份均识别到一般除外块，Youi 识别到 69 个一般除外块，
并修正了识别遇到 `Claiming` 章节时未停止的问题；导航/页脚不再作为条款计入。
AAMI 两份目前只识别到 excess 叠加提示，**其一般除外章节尚不在这个检测器的有效覆盖范围内**，不能据此自动判定完整。
这些只是检测器覆盖观察，没有新增模型输出，也不能当成各文档完整性的评测分数。

## 文件与使用

- 新版：`outputs/car_insurance/schema_trial_20261005/schema_review_v4.json`。
- 输出 contract：同目录 `extraction_contract_v4.json`。
- 来源记录：同目录 `revision_v4_provenance.json`。
- 离线原文清单/旧结果诊断：同目录 `source_coverage_audit.json`。
- 构建器：`src/car_insurance/schema_revision_v4.py`。
- 原文检查：`src/car_insurance/source_coverage.py`。
- 回归：`tests/test_car_schema_revision_v4.py`。

验证结果：选定的原有及新增离线回归通过；最后补充章节结束/导航负例后，v4 专项 **26 项测试全部通过**。
另已验证 v4 生成文件与当前构建器一致、v3 源文件哈希未变、保存的 QBE 清单与最终检测代码一致。

在 car-insurance worktree 生成新候选稿（已存在目标会拒绝覆盖）：

```powershell
..\..\.venv\Scripts\python.exe -X utf8 -m src.car_insurance.schema_revision_v4 --source outputs/car_insurance/schema_trial_20261003/schema_review_v3.json --output-dir outputs/car_insurance/schema_trial_20261005
```

后续真实抽取须显式用 v4 的 schema 路径，并继续提供此前 parser_review 指纹记录。输出到新目录，不覆盖 10 月 3 日那次结果。
只用通用 JSON Schema 工具检查文件，不会运行依赖原文的完整性检查；应通过实际 SchemaExtractor 运行。

## 仍须说明的边界

- 检测只覆盖显式识别的一般除外章节和选定高可信提示，不是所有保险措辞/版式的完整识别器。清单为零不代表没有除外条款。
- 完整引用并不证明模型概括正确；例外条件的逻辑、档位适用范围、不同条款之间的关系仍需人工复核。
- 尚未加入第二阶段专门抽取，因为先验证现有单次流程加检查是否足够，避免无依据增加 API 调用。
- 离线测试证明“这些缺陷会被检查拦住”，不能证明真实模型每次都能生成正确答案。同样本重跑已失败；下一步先完善离线诊断/修复流程和共享额度回归，不自动追加付费调用。
