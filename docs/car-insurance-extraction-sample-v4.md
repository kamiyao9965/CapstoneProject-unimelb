# v4 同样本重跑：QBE TPPD（2026-10-05）

结论：**真实调用完成，但抽取验收失败；有内容改善，也有回退，不能发布为已审核产品数据。**

## 运行与复验

- 用户以 “lets go” 授权对同一开发样本进行 v4 重跑。
- 输入：`data/car_insurance/development/qbe/pds/qbe_tppd_1123.pdf`，40 页；没有使用 holdout/test。
- 模型：openai/gpt-5；schema 为 `schema_trial_20261005/schema_review_v4.json`，仍是候选稿。
- PDF SHA、解析审核指纹、模型输入原文与 v3 相同。提示、schema 和检查逻辑改为 v4；不是单变量实验。
- 运行前冻结 schema、源代码哈希、41 个原文清单单元；运行后检查相关代码未变，v3 结果未改写。
- 完成时间：2026-10-05 07:25:26 UTC。抽取总计时 799.793 秒，约 13 分 20 秒。
- 实际生成 3 次：初始生成 + 2 次修复；达到上限后停止，没有额外付费试跑。
- Tokens 合计：input **180,870**；output **81,552**；total **262,422**。不估算货币费用。
- 日志每行的 `duration_seconds` 是同一逻辑试跑的总时长，不能把三行时长相加。
- 没有成功的 `qbe_tppd_1123.json`。只有原始响应、错误记录和离线诊断，不得将 `response_3.json` 当作已验收产品。

输出目录：`outputs/car_insurance/extraction_sample_v4_20261005_qbe_tppd/`。

| 次数 | 本次实际拦截原因 | 离线补查 |
| --- | --- | --- |
| 1 | `tables: column mismatch`：表头 3 列，行内只有 2 个 cells，名称单独放在 label | “Other loss or damage”的例外未结构化；另有证据位置/完整性问题 |
| 2 | `scoped_exceptions: exception cue without explicit exception` | 表格列数已修好；轮胎/机械故障的 unless 例外仍无 exceptions；还把普通 block ID 当作清单 ID |
| 3 | `tables: column mismatch` 再次出现 | 轮胎/机械故障已有 exceptions，但表格退步；完整性检查仍有 11 条诊断消息 |

业务校验遇到首个错误即中断，所以这三轮实际都未走到运行时完整性检查。
`comparison.json` 在运行结束后独立执行了该检查，保存了每轮的其他问题。
第三轮的 11 条消息不是 11 个独立遗漏：其中 3 个原文块各被同时报作“证据不匹配”和“未覆盖”，另有 5 个例外证据完整性问题。
不能把这些消息数换算成准确率，也不能把 41 个启发式清单单元当成人工 gold。

## S1–S5 内容对照

以下以**最后一次未通过验收的原始响应**为主，对照 v3 和开发 PDF 原文。页码为物理 PDF 页码。

| 项目 | v3 | v4 第三轮观察 | 判断 |
| --- | --- | --- | --- |
| S1 司机除外的例外，PDF 14 | 无例外 | `rule_driver_exclusions.exceptions` 包含“不知情”条件，保留本车原本受保损失，第三方责任不恢复 | 核心语义改善；例外引用仍截掉追偿句，父规则也缺完整该块证据，未通过证据验收 |
| S2 全局规则，PDF 14–17 | 仅 3 条规则 | 11 条，补入故意/鲁莽/欺诈、预防措施、战争/核风险、磨损/机械故障、车辆状况、制裁和法律限制；轮胎/机械故障例外在第三轮才出现 | 主题覆盖改善，不等于全部条款及适用范围已正确；多处例外仍只引用局部句子 |
| S3 租车合理日费用，PDF 13 | limits 为空 | `reasonable_costs` + `per_day`，数值为 null；14 天上限及停止条件保留 | 此项定向内容检查通过；没有编造固定日额 |
| S4 换车保障转移，PDF 11 | 缺失 | 独立 `change_of_vehicle_cover`；售出/处置起算，days/lte/14；通知要求保留 | 核心内容改善；模型重排了侧栏与正文混合的输入，证据字符串不匹配原始块，未通过证据验收 |
| S5 excess 叠加，PDF 26 | 3 项 combination_rule 均为空 | 年龄、附加保单、附加司机 excess 均填叠加规则；保留 learner driver 豁免，金额仍 schedule_specific/null | 此项定向内容检查通过；未相加未知金额 |

S4 体现检查器的限制：PDF 上确有该完整语句，但当前输入块夹杂侧栏文字；“与输入块不完全匹配”不一定意味着条款含义错了。
同样，把司机例外放在嵌套 exceptions 的 evidence 而未在父规则重复引用，也会被当前检查拒绝。
后续应明确证据层级、原文块与可读摘要的职责，而不是直接删除检查或人工改模型结果。

## 已有能力的回归检查

保留的重点：一个 TPPD 产品、Fire & Theft 仍为一个 addon；未凭标题错误生成 Comprehensive/TPFT 产品。
责任金额仍为 AUD 30,000,000、per_incident、aggregate；租车 14 天与司机年龄小于 25 岁保留。
PDS 准备日期 2023-07-31、版本 QM8506-1123、生效日期 null 保留。

但发现以下退步，不能以 S3/S5 改善掩盖：

1. **R1：共享额度关系丢失（高优先级）。** v3 有责任与 Fire & Theft 两个池，v4 三轮均没有池（首轮 []，后两轮 null）。
   第三轮 AUD 30m 只挂核心责任，替代车责任和拖车责任的 limits/limit_pool_ids 都为空，未表达它们共用这一个事故总额。
   Fire & Theft 的相同限额则在各事件重复存储。PDF 10–12 页能支持共同归属，应恢复结构化关系，而不是只保证数字存在。
2. **R2：attempted theft 的赔付上限不完整（高优先级）。** 第三轮 fire/theft 各有 repair 与 total-loss 条件限额，attempted_theft 只有 repair 上限。
   PDF 12 页的同一选项赔付规则适用于 fire、theft、attempted theft；该分拆输出遗漏了 attempted theft 的 total-loss 分支。
3. **R3：无保险司机额度表达发生变化，待复核。** v3 为统一 lesser_of(5000, market_value)，第三轮拆成 repair 固定上限与 total-loss lesser_of 两个条件限额。
   5000 和 market_value 均保留，但条件分支与适用口径不能仅靠 JSON 合法性判断；需针对 PDF 11、20 页复核，不能简单宣布“金额无回退”。

本次没有验证真实 percentage 或共享池子限额场景；本样本失败也不是整个模型/vertical 的总体准确率。

## 为什么自动修复不稳定

- 表格的 `label` 与 `cells` 表达容易混淆；现行规则要求每行 cells 数等于 columns 数，但没有在报错中给出表/行路径、预期数和实际数。
- v4 的例外错误也没有指出具体 `rule_id`。第二轮实际缺的是 `ge_other_loss.exceptions`，模型只得到笼统提示。
- `src/common/structured_output.py` 当前每轮发送原始输入加**上一轮错误**，没有带入上一轮 JSON，也没有保留此前错误历史。
  第三轮针对例外重写全文时，第二轮已修好的表格又坏了。这是本次观察，不代表每次重试必然如此。
- 第一层提前抛错，掩盖了同一响应中的后续问题；本次通过独立离线诊断才同时看到 ID/证据问题。
- 共享池验证可以验证“填了的引用是否自洽”，但不能保证模型一定发现原文中的共享关系。

## 下一步：先离线修工程，再决定付费复跑

2026-10-05 后续：下列离线改进已实施并回放旧响应，见 [运行时诊断与修复 r2](car-insurance-extraction-diagnostics-r2.md)。尚未进行新的付费复跑，本报告记录的三轮失败不变。

1. 明确表格 row label/cells 约定，报错包含完整字段路径、实际/预期列数及短示例。
2. 修复流程保留前轮候选与已知错误，或采用可验证的定点修复；增加“修复后不重现旧错误”的离线测试，并控制上下文/token 增量。
3. 合并安全可执行的校验诊断，精确指出缺失 exception 的规则和来源 ID；避免一轮只发现一个低层错误。
4. 对证据检查区分“条款确实漏掉”和“证据放置/原始块格式不符”；保持页码与来源绑定，不用宽松相似匹配掩盖遗漏。
5. 将共享额度池、三个 Fire & Theft 事件的共同赔付规则加入开发集内容回归，再用同一文档复跑一次。

以上是后续建议，本次未变更运行代码/schema，也未启动第四次付费调用。暂不扩大到其他开发文档或 holdout/test。

## 留存与查看

- `run_plan.json`、`schema_snapshot.json`、`extraction_contract.json`：配置与指纹。
- `source_representation.md`、`source_checklist.json`：原文与清单。
- `request_1/2/3.json`、`response_1/2/3.json`：实际请求与未人工改写的原始返回。
- `run_status.json`、`token_usage.jsonl`、`errors/`：失败与用量。
- `comparison.json`：每轮独立诊断与 v3/v4 内容索引，`v4` 为 null，表示没有成功产物。
- `content_spot_check.json`：本次定向检查结论；代理审查，不是人工 gold 或 schema 审批。

所有 outputs 仍由 Git 忽略。本次报告及此前 v4 代码改动尚未 commit/push；不要把本地试跑资料误认为已经上传 GitHub。
