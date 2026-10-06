# review_v5：保单级共享规则、省略号诊断、超时（2026-10-06，零调用）

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

## 尚未处理

- AAMI Third Party 的 5 组例外范围口径（见 v4r4 文档人工复核），检查器仍无法判断；可做 “(but not the driver” 类窄检查。
- 报错路径归属：某个清单 ID 只要有一个产品引用不全，报错会挂在所有引用它的路径上（含引用正确的产品）。
