# 待修复事项

记录已经确认、但暂时没有处理的问题。每一项写清现象、原因、建议做法和验收方式，处理完后移到"已处理"。

## 1. 提取时启用 OpenAI 严格结构化输出

**状态**：待处理（2026-09-16 记录）。临时缓解见文末"已处理"第 1 项。

**现象**

- `outputs/travel_insurance/extraction_usage.jsonl` 中 2026-09-15 的完整批次使用 GPT-5、MinerU 和 `canonical_approved.json` 处理 19 份 PDS，共发起 61 次模型调用：19 次最终通过校验，42 次未通过校验。按当时的标准非缓存单价估算，42 次失败调用约占总模型费用的 68%。
- 原先记录的“47 次调用、33 次格式失败、5 份 PDS 没有结果”是后续补跑前的中间状态；当前 `outputs/travel_insurance/extractions/run-gpt5` 已有 19 份结果，评估修复效果时应以完整 usage log 为基线。
- 报错都是格式问题，不涉及数据内容：
  - 最外层多出 `__typename`、`__proto__`；
  - `_document_notes` 写成 `__document_notes__`；
  - `_unfilled` 里字段名重复。

**原因**

- `strict` 参数并非没有接入。`SchemaExtractor` 会把 `structured_output_strict` 传给 `StructuredOutputSpec`，但 `src/schema_application/extractor.py` 当前只要发现任意 `list[object]` 或 `required: false` 字段就把它设为 `False`。Travel schema 有 13 个列表字段和大量可选字段，因此这批请求实际发送的一直是非严格模式。
- 列表字段编译出的合同是 `oneOf: [null, array of {"type": "object", "additionalProperties": true}]`，子对象不封闭，不满足 OpenAI 严格模式的要求。
- 这不是 JSON 解析器把 Canonical Schema “解析坏了”。当前 Canonical 字段模型只保存 `type: list[object]` 和一段自然语言描述，没有保存列表元素的属性、类型和必填规则；`compile_canonical_extraction_contract` 没有足够信息可以编译出封闭的子对象，只能按现有设计退化成开放对象。缺口同时存在于 Canonical Schema 表达能力和运行时合同编译器中。
- 严格模式关闭后，模型只是参考 schema 输出，不受强制约束。提取 prompt 里已经写了 `Never output "__typename"`（`tests/test_travel_schema_migration.py` 有断言），但光靠提示压不住。

**建议做法**

1. 扩展 `contracts/canonical_schema.schema.json` 的字段定义，让 `list[object]` 必须携带一个明确的元素合同（名称可在实现时确定，例如 `item_schema`）。元素合同至少包含固定的 `properties`、每个属性的类型和可空性；不能再只用自然语言描述内部结构。
2. 在新的 Travel Canonical candidate 中为 13 个列表字段定义固定的子字段结构：`additionalProperties: false`，所有子字段都列入运行时 `required`，原文缺失时用 `null`。13 个字段是 `waiting_period_rules`、`excess_rules`、`cruise_benefits`、`optional_add_ons`、`coverage_benefits`、`sub_limits`、`included_activities`、`optional_activity_cover`、`excluded_activities`、`depreciation_rules`、`general_exclusions`、`benefit_exclusions`、`source_evidence`。
3. 调整 `compile_canonical_extraction_contract`：标量字段继续从 Canonical 字段定义编译；`list[object]` 从元素合同编译 `items`；运行时合同把所有属性列入 `required`，通过包含 `null` 的类型表达业务上的可选。Canonical Schema 中的业务含义 `required` 不需要因此全部改成 `true`。
4. 不再根据“是否存在 `list[object]` 或可选字段”静默关闭严格模式。先编译完整运行时合同，再由 OpenAI schema 投影检查兼容性；Travel 的批准合同必须得到 `strict: true`。若合同不兼容，应在发起模型调用前明确失败，不能自动回落到非严格模式。
5. 保留 `src/common/model_provider.py` 中 `_project_openai_schema` 对开放对象、非必填属性和不支持关键字的拒绝，让它成为防止错误合同进入线上调用的最后边界。
6. Canonical Schema 结构变化后创建新版本 candidate，经过人工复核和批准；不得直接修改已批准版本。运行时提取合同变化后，用代表性 PDS 重新提取。数据库中的列表字段仍然使用 JSONB，不需要因为子结构封闭而拆表。

**提示词策略（严格模式完成后）**

- Prompt 负责解释字段语义，不再承担 JSON 语法约束。保留“一份 PDF 中每个独立计划各输出一个对象”“缺失值使用 `null`”“不要从原文之外推断”等规则。
- 按第 2 项删除 `product_name` 文档内唯一、把档次拼进名称以及错误的 `plan_tier` 要求；明确系列名和 `plan_name` 的职责。
- 先用评估确认稳定出现的语义混淆，再决定是否增加 few-shot。Gold/Silver 多档次、总限额与分项限额等问题可以各用一个很小的示例；不加入整份、几十字段的完整 JSON 示例。
- 对无示例、一个微型示例和两个微型示例做固定样本对比，记录首次通过率、字段正确率、输入/输出 token 和单文档成本。只有质量收益稳定高于新增 token 成本时才保留示例。

**验收**

- 离线测试直接断言 Travel 的 `StructuredOutputSpec.strict is True`；每个嵌套对象均为封闭对象，运行时合同不存在未定义元素结构。
- 用 3~5 份差别较大的 PDS 试提取，首次调用通过率至少达到 95%，19 份同规模批次的模型调用目标为 19~23 次。
- 与 `run-gpt5` 相比，平均重试次数和每份的输出 token 明显下降。
- 离线测试覆盖：严格模式合同的生成、列表子字段结构、OpenAI schema 投影。

## 2. 简化 Travel 抽取记录的身份和入库规则

**状态**：待处理（2026-09-20 重新评审）。本项取代 2026-09-16 记录的“强制 `product_name` 唯一并把档次拼进名称”方案。

**现象**

- 当前 `validate_canonical_extraction_identities` 要求同一份 PDF 中标准化后的 `product_name` 唯一。这会把产品系列名和数据库身份绑在一起，Gold、Silver 等档次共用系列名时必须人为改写 `product_name`。
- `src/storage/repository.py` 使用 `vertical + insurer + product_name` 的哈希生成 `product_id`，再使用 `product_id + document_id` 生成 `release_id`。身份规则依赖模型生成的名称，名称改写、纠错或同名档次都可能导致错误合并或拆分。
- 当前重复名称会进入 LLM 修复请求，但“产品名是否唯一”不应消耗额外模型调用。
- JSON Schema 可以验证输出结构、类型、枚举和必填字段，但不能证明抽取值与 PDF 原文一致。例如保额类型正确但数值错误时，仍可能通过结构校验。

**决定与范围**

- 系统的责任收窄为“把 PDF 转成结构合法、可追溯的抽取记录”，暂不解决跨 PDF、跨年度的真实产品实体归并。
- 一份 PDF 的一次抽取返回一个顶层 JSON；`products` 数组中每个通过运行时 JSON Schema 的对象都作为一条独立产品记录入库。
- 数据库为每条产品记录分配无业务含义的自增 `product_id`。不引入 `canonical_id`，不把 vertical、insurer、product family、plan name 或 product type 编码到主键中。
- `product_name` 和 `plan_name` 都不作为唯一键。删除 Canonical 抽取中的 `product_name` 文档内唯一性校验，不因名称重复发起 LLM 修复请求。
- 使用 `run_id + document_id + item_index` 标识一次抽取中的数组项，并作为重复导入的幂等约束。`item_index` 是该对象在 `products` 数组中的位置。
- JSON Schema 通过的记录只标记为“结构合法、可入库”，不声称业务事实已被证明正确。保留 run、文档、provider、model、schema version 和原始 artifact 以便追溯。
- 不增加 candidate/product 映射表、自动实体归并、复合产品编码或多层 product-family/variant 模型。同一真实产品在两次抽取中出现两次是当前范围内可接受的。
- 不删除与名称唯一性无关的边界校验，例如严格 JSON 解析、JSON Schema、Canonical Schema 批准状态、vertical/schema-version 匹配、文档来源和数据库事务校验。

**实施建议**

1. 将 `products.product_id` 改为 PostgreSQL 生成的自增代理主键，同步调整外键和 SQLAlchemy 类型。保留字段名 `product_id`。
2. 停止调用 `deterministic_product_id(vertical, insurer, product_name)`；不再从模型生成的名称推导数据库主键。
3. 为入库记录保存 `run_id`、`document_id` 和 `item_index`，并建立幂等唯一约束；重复加载同一 artifact 不应创建新行。
4. 从 `SchemaExtractor` 和 `compile_canonical_load_plan` 的 Canonical 路径移除 `product_name` 重复拒绝；保留非空、类型、枚举和必填约束在 JSON Schema 或本地合同校验中。
5. 删除提取 prompt 中“`product_name` 必须在 `products` 内唯一、必须把档次拼进名称”的要求；将错误的 `plan_tier` 更正为 `plan_name`。
6. 继续保存每份 PDF 的完整抽取 artifact。数据库表只是可查询投影，不覆盖、猜测或静默修改 LLM 返回的业务值。
7. 实现后同步更新 `docs/decisions/001-postgresql-hybrid-storage.md`、`docs/decisions/002-approved-canonical-schema-authority.md`、`docs/architecture.md`、`README.md`、`api.md` 和 `docs/user-guide.md`。

**事实质量与人工复核**

- LLM 可能输出类型正确但与 PDF 原文不一致的值。本项不在入库前自动猜测或更正这些值。
- 操作人员可以使用数据库管理工具、SQL 查询或视图检查已入库数据。可以增加不修改数据的异常报告，例如负数金额、超出业务参考范围的数值、异常高值、大量缺值或同份文档内明显冲突的记录。
- 人工检查结果应作为独立审核信息保存，不直接改写原始抽取 artifact。
- 后续可增加 teacher model 的 LLM-as-a-judge 离线评估，让评估模型同时查看 PDF 证据、字段定义和抽取值，输出结构化的正确性、证据支持和不确定性评分。它不进入每份文档的同步抽取或入库链路，不因低分自动重试，也不自动覆盖业务数据。
- LLM-as-a-judge 是质量筛查和人工复核排序信号，不是 ground truth。引入前应用一小批人工标注样本校准 teacher model，并记录评估使用的 provider、model、prompt 和 schema version。
- Judge 应按固定评估集、发布前回归或抽样批次运行，费用写入独立 usage log，不能混入生产抽取成本。低分结果进入人工复核队列；人工结论与原始抽取 artifact 分开保存。

**非目标**

- 本次不实现跨文档产品去重或同义名归并。
- 本次不建立 product family、plan variant 和 product release 的新层级。
- 本次不把 LLM-as-a-judge 结果作为阻止入库的强制门槛。
- 本次不使用 teacher model 自动修改抽取值。

**验收**

- 包含 Gold 和 Silver 两个对象、且两者 `product_name` 相同的合法 JSON 能通过抽取边界，不发起名称唯一性修复请求。
- 两个对象入库后获得两个不同的自增 `product_id`，且 `product_id` 不编码任何业务字段。
- 重复加载同一 `run_id + document_id + item_index` 不会创建第二条记录；新的 run 可以保存新的抽取结果。
- JSON Schema 不合法的结果仍然失败；是否重新调用模型按第 3 项的失败分类策略决定，名称重复本身不得触发修复。
- 入库保留 vertical、schema version、run、源 PDF、provider、model 和原始 artifact 的可追溯性。
- 至少提供一个只读异常查询或报告示例，支持人工找出需要复核的数值；不修改原始抽取数据。
- 如实现 LLM-as-a-judge，其输出必须独立保存、可追溯且经过小样本人工校准，不写回抽取值。

## 3. 按失败类型决定是否重试

**状态**：待处理（2026-09-20 记录）。在第 1 项严格结构化输出和第 2 项删除名称唯一性修复的基础上实施。

**现象**

- `run_structured_output` 当前把严格 JSON 解析错误、JSON Schema 错误和业务校验错误都压成 `{path, message}`，随后对所有这类错误统一追加修复提示，最多再调用模型两次。
- `ProviderResponseError`、PDF 解析失败和后台轮询超时走不同异常路径，但没有统一的失败分类和重试决策记录。仅靠错误消息文本无法稳定判断是否应重试。
- OpenAI、Anthropic 和 DeepSeek 客户端已经各自配置传输层重试。传输失败、模型输出修复和整份文档重新运行是三种不同动作，不能共用一个计数器。

**决定**

不要让模型判断“自己是否值得重试”，也不要依赖字符串关键字猜测。错误产生的位置必须给出类型化的 `failure_kind`，由确定性的策略表选择动作：

| `failure_kind` | 可观察信号 | 默认动作 |
| --- | --- | --- |
| `transient_transport` | 连接中断、HTTP 408/429/5xx 等 SDK 异常 | 交给 provider SDK 指数退避；耗尽后结束本次任务，不发送 LLM 修复提示 |
| `poll_timeout` | 后台 response 仍为 pending 且超过等待时间 | 优先继续查询同一个 response id；不能确认状态时停止，避免重新创建可能重复计费的生成 |
| `output_truncated` | provider 明确返回 `incomplete/max_output_tokens` 或 `finish_reason=length` | 最多重新生成一次，并明确提高输出上限或缩小合同；记录为重新生成，不记作格式修复 |
| `json_parse` | 非严格 provider 或历史模式返回非法 JSON | 最多允许一次格式修复；严格模式下视为 provider/适配器异常并停止 |
| `schema_validation` | 本地 JSON Schema 返回路径和关键字错误 | 非严格兼容路径最多修复一次；`strict: true` 下视为合同投影或适配器缺陷并停止，不盲目重试 |
| `business_validation` | 名称唯一、范围合理性或其他业务规则失败 | 不发起 LLM 格式修复；需要的业务规则转为入库边界、异常报告或人工复核 |
| `refusal_or_filter` | provider 明确拒绝、空响应或内容过滤 | 不自动重试，保存可追溯失败记录并交人工判断 |
| `source_parse` | PDF 无可用文本、解析器失败或来源文件损坏 | 不调用模型，修复/更换解析路径后由操作人员重新运行 |
| `content_uncertain` | 数值可疑、证据不足、PDF 含糊但结构合法 | 接受为结构合法记录并标记复核；由人工或离线 Judge 评估，不自动重抽 |

**实施建议**

1. 为结构化输出错误增加稳定的枚举或异常类型，至少区分 JSON 解析、Schema 校验、业务校验、截断、拒绝和 provider 暂时错误；不要从面向用户的 message 反向解析类别。
2. 把 provider 传输重试、后台 response 轮询、LLM 格式修复和文档级重新运行拆成独立策略。每层都有自己的上限，并保证一层不会重复执行另一层已经处理的重试。
3. 第 1 项完成后，Travel 严格模式默认不应再需要两次格式修复。保留兼容旧 provider/旧 artifact 的单次修复入口，但不要把它作为正常路径。
4. usage log 增加 `failure_kind`、`retry_action`、`retry_reason` 和本次是否产生新模型调用；继续记录每次调用的 token、耗时和校验结果，以便单独计算重试成本。
5. 失败 artifact 保存稳定的错误码和安全的结构化细节，不保存原始 PDF 内容、密钥或完整模型响应。

**验收**

- 离线 fake provider 测试覆盖表中每种失败类型，并精确断言调用次数和采取的动作。
- `product_name` 重复、可疑金额、内容不确定、拒绝和 PDF 解析失败均不会触发 LLM 格式修复。
- 截断最多触发一次受控重新生成；轮询超时不会直接创建第二个可能重复计费的 response。
- usage log 能回答每次重试由什么错误触发、采用了什么动作、额外消耗多少 token。
- 19 份同规模批次的格式失败目标为 0~4 次，总模型调用目标为 19~23 次。

## 已处理

### 1. 提取输出的格式噪音在校验前自动清理（2026-09-16）

- `run_structured_output(..., drop_structural_noise=True)`：校验前删除合同未声明的双下划线键，拼错的已声明键在正确键缺失时改回来，`uniqueItems` 字符串数组去掉重复项；每次清理都会打印日志，提取值不做任何修改，其他格式错误仍然判失败。
- `SchemaExtractor` 默认开启。这是临时缓解，不能替代第 1 项的严格模式。
