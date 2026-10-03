# v3 首次真实抽样：QBE TPPD（2026-10-03）

结论：**真实模型抽取和两层校验成功；内容抽查发现遗漏，不作为已审核产品数据或 benchmark 成绩。**

## 运行记录

- 样本：开发集 `data/car_insurance/development/qbe/pds/qbe_tppd_1123.pdf`，40 页。
- 选择方式：定向选择含 Fire & Theft 选项的文档，检查产品边界；不是随机抽样，也不代表总体准确率。
- Schema：`schema_trial_20261003/schema_review_v3.json`，仍为 review_candidate_not_approved。
- 解析质量：沿用用户接受的 parse_check_v8 内容，运行前验证 PDF、解析内容和审核指纹一致。
- 模型：openai/gpt-5，输入为本地解析后的 Markdown，不上传原始 PDF。
- 完成时间：2026-10-03 07:07:15 UTC；抽取计时 159.377 秒。
- 实际模型生成调用：1 次，未触发结构修复。配置最多允许一次生成加两次修复，并未全部使用。
- Tokens：input 53,351；output 17,237；total 70,588。未估算货币费用。
- 结果：1 个产品、1 个附加险、2 个共享额度池、4 条自付额、3 条全局规则。
- JSON Schema 校验、profile 业务校验和随后离线复验均通过。离线复验没有额外模型调用。
- 未读取 holdout/test 文档，未跑 benchmark，未修改 schema/prompt 来迎合本次结果，未人工修补模型输出。

输出目录：`outputs/car_insurance/extraction_sample_v3_20261003_qbe_tppd/`。

| 文件 | 用途 |
| --- | --- |
| `qbe_tppd_1123.json` | 包装后的模型抽取结果，产品在 `data.products[0]` |
| `schema_snapshot.json` / `extraction_contract.json` | 本次实际使用的 schema 与输出 contract |
| `run_plan.json` | 样本选择、模型及输入指纹 |
| `source_representation.md` | 试跑准备时记录的输入文本；`request_1.json` 为实际发送的文本与提示 |
| `response_1.json` | 未人工改写的模型原始响应及模型用量 |
| `token_usage.jsonl` / `run_status.json` | 用量、校验及运行结果 |
| `content_spot_check.json` | 以下抽查发现的机器可读清单；不是人工 gold 标签 |

## 核对成功的重点

页码均指 PDF 的物理页码；本文件 PDF 页码通常比印刷页码大 1。

| 项目 | 原文位置 | 输出核对 |
| --- | --- | --- |
| 产品与附加险 | PDF 10、12 页 | 只生成 TPPD 产品；Fire & Theft 是 addon，未额外生成 TPFT/Comprehensive 产品 |
| 每次事故责任总额 | PDF 10–11 页 | `tppd_liability_pool` 为 AUD 30,000,000、per_incident、aggregate，核心责任、替代车责任和拖车责任引用同一池 |
| 无保险司机损失保障 | PDF 11 页，理赔说明见 20 页 | 提取 fixed 5000 与 market_value 的 lesser_of；未编造市值，并保留未过错/对方身份等条件 |
| 失窃后租车时长 | PDF 13 页 | optional、关联 Fire & Theft；days/lte/14，停止供车条件和需要授权也保留 |
| 司机年龄门槛 | PDF 26 页 | age excess 使用 years/lt/25、reference_kind=driver_age；具体自付额保持 schedule_specific/null |
| 文档身份 | PDF 1、3、40 页 | 记录标题、31 July 2023 准备日期、QM8506-1123 版本；未推断未知生效日期 |

上述是定向内容抽查，不是全文正确性证明。责任池成员范围和组合限额的适用条件仍应由用户最终复核。
本样本两个池均无子限额，且没有抽出的 percentage 金额，因而**不能据此宣称真实数据上的子限额/百分比功能也已验证**。
这些功能目前仍只有离线合成测试覆盖。

## 发现的问题与下一步

### S1 — 司机除外的例外条件遗漏（高优先级）

- 原文 PDF 14 页 Driver：若投保人没有理由怀疑司机存在上述情况，原本受保的自身车辆损失仍可赔；司机造成的第三方责任不因此恢复。
- 输出位置：`data.products[0].policy_rules[rule_id=rule_driver_exclusions]`。
- 当前只概括“无照/酒驾等不赔”，conditions 为空，未保留这个例外及自身损失/第三方责任的区别。
- 后果：结构正确但改变保障含义，可能把原文有条件可赔误呈现为绝对不赔。
- 后续方向：要求每条除外与紧邻的 exception/however/unless 条款共同抽取，按保障范围记录；增加该开发样本的回归检查。

### S2 — 全局规则抽取不完整（高优先级）

- 原文 PDF 14–17 页还有故意/鲁莽/欺诈及其例外、合理预防、战争/核材料、磨损/机械故障、车辆状况、制裁等规则。
- 输出 policy_rules 仅 3 条：车辆用途、司机除外、网络事件；上述多个主题缺失，已有用途规则也只是部分概括。
- 这些文字存在于本次发给模型的解析文本，不能简单归因为“PDF 没识别出来”。
- 后续方向：建立原文章节/条款清单核对覆盖情况；必要时将保障与全局规则分阶段抽取。不要直接把模型结果当标注。

### S3 — 租车“合理日费用”只留在证据里（中优先级）

- 原文 PDF 13 页：reasonable daily cost。
- 输出 `hire_car_benefits[benefit_id=ft_hire_car_after_theft].limits=[]`，证据和文档表格中保留了这段话。
- 后果：按结构化金额字段查询时丢失“合理费用、按日”的信息，虽然人读证据还能看到。
- 后续方向：明确 reasonable_costs 也应生成 limit（per_day），而不是只有固定数字才填；不得捏造固定日额。

### S4 — Change of car 的 14 天保障转移遗漏（中优先级）

- 原文 PDF 11 页明确，在卖掉/处置原车后，保障自动转到替换车辆，最长 14 天。
- 产品结果没有这一记录；`other_coverages` 和 `temporary_replacement_vehicle_cover` 均为 null，其他规则亦未表达。
- 它不是“租车最多 14 天”，也不是维修时临时替代车保障，不能只因都有 replacement/14 days 就混在一起。
- 后续方向：先为“换购车辆/保障转移”确定清晰归属，再补覆盖测试；这也是目前分类的一个边界检查点。

### S5 — 自付额叠加规则未进入专用字段（中优先级）

- 原文 PDF 26 页：年龄、附加保单、附加司机 excess 明确可在 basic/其他适用 excess 之上叠加。
- 模型把这层意思写进 conditions/evidence，但相应 `combination_rule` 都为 null。
- 后果：不是原文信息完全丢失，而是专用字段漏填；下游只读该字段会误判。
- 后续方向：增加“自由文本已表达某规则时，对应专用字段必须一致填入”的检查。金额仍以保单表为准，不能凭这份 PDS 自动加出一个数值。

## 使用结论

可以继续把 v3 用于开发集诊断，但不建议立即批量跑所有文档或测试集。
先处理 S1/S2（例外条件和完整性），再处理结构化漏填与换车保障归属；经用户授权后对同一开发样本重新试跑，保留本次作为前后对照。
本次任务止于“一份真实样本 + 保存结果 + 内容抽查”，没有自动开始下一轮付费调用，也没有把候选 schema 升级为已批准版本。
