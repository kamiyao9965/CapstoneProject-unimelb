# Car schema v3：回应人工审核意见

日期：2026-10-03。状态：**修订候选稿，未批准，未调用模型**。
保留原始 `schema_draft.json` 和 `schema_review_v2.json`；本次没有读取 holdout/test 文档或修改数据划分。
新增结构依据用户提供的审核意见，不把“建议支持的保障”当作任何 PDS 确实提供的保障。

## 在哪里继续审核

打开 `outputs/car_insurance/schema_trial_20261003/schema_review_v3.json`。
按以下顺序搜索文件中的键：

1. `taxonomies`：每个分类的包括/不包括定义，特别是替代车辆、拖车和第三方责任的边界。
2. `limit_pools` 以及 `$defs.limit_pool`：共享额度归属与子限额。
3. `$defs.amount_term`、`$defs.limit`：百分比和组合金额的表达。
4. `validation_rules`：哪些规则确实由运行时验证器执行。
5. `document_evidence_schema` 与 `$defs.document_evidence`：文档身份信息，不属于每个产品的 `fields`。
6. `excesses`、`policy_rules`：自付额范围、年龄条件、起保免责期。

本次仍不接入旧 consensus 审核 UI；没有伪造 review queue 或 human-approved 状态。
review_v3 的结构/分类/规则和运行时代码绑定；需要修改这些定义时应同步修改构建器、验证器和测试，重新生成新版本，不能只改 JSON 绕过校验。
普通字段说明可以编辑，但仍需确认与规则一致。它不是可直接无损转换为旧 canonical schema 的文件。

## 哪些意见是新增，哪些是把已有实现显式化

| 审核点 | v3 处理 |
| --- | --- |
| 分类只有 Owned by | 每一类都有 Includes/Excludes 和归属；替代车费用、替代车自身损坏、替代车第三方责任明确分开 |
| `document_evidence` 找不到 | v2 已在编译器定义；v3 的源 schema 显式包含 `document_evidence_schema`，指向本地 `$defs.document_evidence` |
| PDS 版本/日期缺失 | v2 编译结果已有 preparation_date/effective_date/version_code；v3 把它们显示在源 schema，并补 vehicle_types、适用说明、页码证据 |
| fixed/option 双向一致未验证 | v2 已有业务校验；v3 进一步提供 `validation_rules` 清单，与业务验证器绑定并有正反例测试 |
| 共享金额归属不清 | 删除 v3 的 `shared_with`，新增产品级 `limit_pools` 和保障上的 `limit_pool_ids` |
| 百分比/较低较高者 | 统一成 `limit.terms` + `combination`；不把百分比降级为 schedule_specific，也不虚构当前市值 |
| excess 适用范围/年龄 | 增加 applicability、applies_to_benefit_ids、applies_to_events、quantities |
| 功能/数量缺口 | 增加无索赔优惠保护、改装配件、宠物受伤分类；policy_rules 支持 waiting_period；数量支持 hours/months/percent 与 reference_kind |
| 可选项边界 | not_available 需明确证据；不同附加险档次用两个 option_id 和同一个互斥组；基础包含和额外可选分条 |
| 证据定义重复 | `$defs.evidence` 只定义一次；金额、数量、池和文档对象同样复用本地定义，不允许远程或循环引用 |

没有采用 `if/then` 把所有业务逻辑硬塞进模型输出 JSON Schema。类型、必需键、枚举及取值范围由 JSON Schema 验证；
跨字段/跨记录的引用和金额一致性由显式 `validation_rules` 对应的业务验证器执行。仅用通用 JSON Schema 工具检查 contract **不等于**跑了所有业务规则。
实际 `SchemaExtractor` 两层都执行，失败走现有结构修复重试/失败记录流程，不会静默当作成功。

## 共享额度：完整的归属约定

`limit_pools` 位于 **每个产品记录内**，不是整个 PDF 的顶层。不同产品/档位不能共用一个额度池。
在 pool 中保存总额与子限额，在保障中只存 pool ID，不复制总额或子限额。

下例是简写的**合成示例**，不是某家保险的实际抽取结果，也不是完整可提交的记录：

```text
产品 A
  transport      limit_pool_ids = [emergency_pool], limits = []
  accommodation  limit_pool_ids = [emergency_pool], limits = []
  limit_pools
    emergency_pool
      member_benefit_ids = [transport, accommodation]
      limit: AUD 750 / per_incident / aggregate
      sub_limits: accommodation -> AUD 500 / per_incident / aggregate
```

- `member_benefit_ids` 与各保障的 `limit_pool_ids` 必须双向一致、引用现有 ID；池至少有两个不同成员。
- 共享是“成员合计消耗同一额度”，不是“各有一份相同额度”；相同数值并不能证明共享。
- 如果另有不同范围的独立限制，例如住宿每天最多 100，可以放在保障的 `limits`，与池一起约束；池内的住宿子限额不能再复制过去。
- 在 period/basis/conditions 一致且都是固定数值时，子限额不能大于池总额。涉及百分比或不同适用条件时，不臆算大小关系。
- 同一组成员、同一个金额表达的重复池会被拒绝。换种措辞的同义重复仍需人工识别。
- 如果一条保障笼统覆盖三个组件，而组件有不同子限额，应拆成可引用的组件保障；不要用一个含混 ID 丢掉子限额归属。
- 共享池也可有 per_person 范围，意为“每人有一个由多个保障共享的池”；不能强制所有共享都只有 aggregate 范围。

## 金额表达

所有限额统一使用以下层次，而不是在不同字段随意摆放金额：

```text
limit_id / period / basis / conditions / source_text / evidence
  combination: single | lesser_of | greater_of
  terms:
    amount_kind / amount_aud / percentage / percentage_of / reference_text
```

| 原文表达（合成示例） | 表达方法 |
| --- | --- |
| 每天 AUD 90 | period=per_day，single，fixed 90 |
| 每天 AUD 90，最多 14 天，总额 AUD 1,000 | 两条独立 limit（日额、总额）及一条 days/lte/14 数量；条件适用时同时约束 |
| 最高保额的 10% | single，percentage=10，percentage_of=sum_insured，amount_aud=null |
| AUD 10,000 或市值取低者 | lesser_of，两个 terms：fixed 10000 与 market_value（金额未知保持 null） |
| 固定金额与保额百分比取高者 | greater_of，fixed 与 percentage 两个 terms |
| 无上限/合理费用/保单表列金额 | 对应 amount_kind，数值保持 null，保留原文，不能用 0 代替 |

`per_claim` 只用于明确按索赔计算；any one event / occurrence / incident 归 `per_incident`；按合同期间归 `per_policy_period`，不能擅自改成自然年。
不同期间的两项约束不能塞进一个 lesser_of；不同条件下的替代安排也不能假装同时生效，须拆成有条件的记录或不同 variant。
嵌套多层公式、无法确定百分比参照、条件关系含混等情况仍可能需要 unknown + 原文 + 人工复核；不宣称已经覆盖任意保险条款数学公式。

## 分类和状态边界

- hire_car_*：租车费用/提供车辆；temporary_replacement_vehicle：替代车本身损坏；substitute_car_liability：替代车造成的第三方责任。
- trailer_cover：拖车本身损坏；caravans_and_trailers_tppd_extension：拖车导致的第三方财产责任。
- third_party_property_damage：主体责任保障；legal_liability_features：单独写明的法律费用/附属责任，不重复主体保额。
- fire/theft/attempted_theft 只能配 fire_and_theft；已知其他事件配 accidental_loss_or_damage；other 必须提供事件原文说明。
- `included` 表示基础保障，存在金额上限不自动变为 limited。
- `limited` 表示风险/服务范围明确受限；`conditional` 表示资格/批准条件导致权益不能视为无条件成立；普通索赔手续不自动归 conditional。
- 需要另选附加险时优先 `optional`，限制写 conditions，不因有限额就改为 limited。
- v2 的 `not_required` 改成 `not_applicable`，仅用于原文明示不适用；“不需要购买”不是“不适用”，不能自动迁移旧值。
- 全局除外只进 policy_rules；保障 exclusions 只放特有除外。验证器拦截相同文本重复，不承诺识别所有改写重复。

## 产品、附加项、自付额

同一 benefit_id 在不同产品记录里重复、不同档位状态不同是允许的；ID 唯一性是**每个产品内部所有保障字段之间**的唯一性。
产品自身名称仍须区分。PDS 中命名档次各一条；不从可选项组合生成新产品。

租车附加险若有可分别选择的 14/21 天两个档次，建立两个 option_id，同一个 option_group_id，selection_rule=one_of，各连到自己的 benefit variant。
如果只是一个选项的不同触发条件，不擅自造两个“可选档次”；保留同一个选项下的保障 variants。
被盗后租车 included、事故后租车 optional 是两条，不合并为 conditional。
`not_available` 只表示明确不提供；null/未提及并不能推出 not_available，也不能推出客户已购买。

excess applicability 为 all_coverages / benefits / events / benefits_and_events / unknown；对应引用列表必须一致。
“玻璃索赔适用”“被盗索赔适用”可结构化关联。司机小于 25 岁写 metric=years、comparison=lt、reference_kind=driver_age；车龄则为 vehicle_age。
起保后若干小时的免责期在 policy_rules.kind=waiting_period，用 hours 与 time_since_policy_start 表达，并关联受影响事件。
不根据字段出现就推断这些条款存在；仍必须保留证据。

## 复现与验证

在 car-insurance worktree 下，离线生成（目标文件已存在会拒绝覆盖）：

```powershell
..\..\.venv\Scripts\python.exe -X utf8 -m src.car_insurance.schema_revision_v3 --source outputs/car_insurance/schema_trial_20261003/schema_review_v2.json --output-dir outputs/car_insurance/schema_trial_20261003
```

产物：`schema_review_v3.json`、`extraction_contract_v3.json`、`revision_v3_provenance.json`。
来源记录包含源 v2 文件 SHA-256 和本次 model_calls=0。源码、源文件及命令一同保留即可复现。

测试：`tests/test_car_schema_revision_v3.py` 使用合成数据、模拟 provider；覆盖正例和错误金额、引用、分类、选项、子限额、缺证据等反例。
本轮选定离线回归 **250 项通过**，其中 v3 新增 40 项；v3 有 35 个产品字段、30 个保障分类和 6 个复用定义。
旧 v2 测试保留。没有真实 API 接受性测试或新一轮真实 PDF 抽取准确率结果。
下一步由你继续审核这个 v3，确认后再选择 development 的少量 PDF 试抽取并逐条对照原文；不能用本次测试通过数替代人工内容审核。
