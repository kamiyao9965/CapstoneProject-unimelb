# Schema 首次试跑（2026-10-03）

结果：首次 discovery 已成功完成，运行器耗时约 112 秒，输出 artifact.status=success。
草稿含 31 个顶层字段、3 种 product_type、30 个 coverage_categories 项。
模型报告 input 131,824 tokens、output 10,427 tokens、total 142,251 tokens；未估算货币费用。
结构校验通过不代表业务字段已被人工审核；没有启动 consensus、产品抽取或 benchmark。
下一步审核字段是否重复、嵌套对象约束是否可执行、产品/可选项区分是否有原文依据，再决定是否进行共识细化。

用户确认："我核实了一下感觉没有什么大问题了，可以启动schema试试了"。
据此接受当前 parse_check_v8 的剩余风险进行一次 discovery 试跑；不表示逐条警告都已修复，
不表示人工批准 schema，也不构成 benchmark 标注。

- 范围：5 份 development PDF（AAMI、QBE、Youi）。未使用 NRMA holdout 或 Allianz test。
- 模型：使用现有配置 openai/gpt-5，发送本地解析的 Markdown；不上传原始 PDF。
- 阶段：一次 discovery 工作流（底层可能有结构校验重试），暂不启动 consensus 或产品抽取。
- 输出目录：`outputs/car_insurance/schema_trial_20261003/`。
- `parser_review.json`：用户试跑授权及每份 PDF/解析器配置/解析页内容的指纹。
- `run_plan.json`：模型、输入清单与目标文件。
- `schema_draft.json`：运行成功时的 schema 草稿产物；不存在时不得认定生成成功。
- `token_usage.jsonl`：成功调用的模型用量记录（如运行器有记录）。

Car 质量门槛仅在显式指定 `CAR_INSURANCE_PARSER_REVIEW` 时读取试跑复核记录；没有该记录，
原警告仍阻止运行。只有指纹完全匹配的文档可放行，换 PDF、解析内容或解析器配置后重新复核。
原警告、quality_review 和旧版解析结果未清除或覆盖。

本地启动脚本 `outputs/car_insurance/start_schema_trial.py` 从父工作区 `.env` 读取选定环境变量，
不打印或复制 key 到报告。已生成草稿后拒绝再次运行；失败后重试仍可能计费，应先查看失败记录。
该脚本不在 Git 跟踪中，不是通用数据采集脚本。

测试：147 项选定离线回归通过，包含试跑授权不能放行复核后发生变化的解析内容。
