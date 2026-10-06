# Car schema 修订 v2（2026-10-03）

[中文原文 / Chinese original](#chinese-original) · [English version / 英文版](#english-version-text)

<a id="chinese-original"></a>

状态：四项检查已落实为候选 schema、抽取约束及离线测试。**不是人工批准版本，也不是 benchmark 标签。**
本次没有调用模型，没有读取 holdout/test 内容。原始模型草稿保持不变。

## 文件与使用

- 原稿：`outputs/car_insurance/schema_trial_20261003/schema_draft.json`，保留原始 artifact 包装。
- 修订稿：同目录 `schema_review_v2.json`，是可传给抽取器的原始 schema 对象，共 31 个产品字段，其中 25 个有可执行的嵌套定义。
- 编译结果：同目录 `extraction_contract_v2.json`，用于验证实际抽取 JSON，不是 discovery 输入。
- 来源记录：同目录 `revision_v2_provenance.json`，记录原稿 SHA-256、修订状态与本次模型调用数 0。
- 生成逻辑：`src/car_insurance/schema_revision.py`；本地生成脚本 `build_revision_v2.py` 拒绝覆盖已存在输出。

这是有意改变结构的新版，旧抽取结果不能简单改文件名后当作新版使用。没有将它设成默认 schema。
后续用共享抽取入口的 `--schema` 显式指定修订稿；实际 PDF 抽取仍受已有 parser review 指纹门槛约束。
先人工确认本次结构，再做少量 development 抽取核对。不自动开始付费抽取、consensus 或最终评测。
该 profile 的字段结构/分类必须一致；后续 consensus 若改变结构，需要同步修订 profile 和测试，不能静默忽略新字段。

## 1. 同一信息只设一个主要归属

| 原字段/问题 | 新位置与规则 |
| --- | --- |
| `coverages` 与专属保障字段重复 | 已有保障只进其专属字段；`other_coverages` 仅允许兜底分类 `other_documented_benefit` |
| 泛化列表中的 business items / campervan contents | `additional_item_benefits`，两个明确分类 |
| `optional_benefits` 再次存额度 | 改为 `available_addons`，只存可选项、适用条件、保障 ID 引用；金额留在保障自身 |
| `eligibility_and_use_restrictions` / `general_exclusions` | 合并为 `policy_rules`，以 `kind` 区分，不在两份列表重复同一全局规则 |
| 每个产品重复元数据、概览表 | 移到产品数组之外的 `document_evidence.metadata` / `coverage_summary_tables`，每份 PDF 存一次 |
| `insurer_legal_name` / `underwriter_name` 含义相近 | 明确为 `issuer_legal_name` / `risk_underwriter_legal_name`；同一主体可承担两个角色，不能仅因值相同删除 |

保留专属字段，避免把所有保障变成一份难核对的大列表。每条保障含 `benefit_id`、`category`、`variant`、原文标题及证据。
相同分类/variant/option 的重复项和重复 ID 会被拒绝。换一种说法、另取一个 ID 的语义重复仍需人工对照原文发现，不能宣称完全自动去重。

## 2. 金额、计算范围与非金额数量分开

每条 `limits` 都有：

- `amount_aud`：澳元数值，不能写成 `"$1,500"`。
- `amount_kind`：`fixed`、`unknown`、`unlimited`、`reasonable_costs`、`schedule_specific`；只有 fixed 带数字，其余为 null。
- `period`：每次索赔、事故、保单期间或每天。
- `basis`：总额、每人、每物品等，与 period 独立。
- `shared_with`：确有共享限额时引用其他保障 ID；限额只存一次。
- 原文、条件与页码证据。

例如 counselling 的 `1500 + per_claim + per_person` 同时保留“每次索赔”和“每人”；funeral 的 `5000 + per_policy_period + aggregate` 是另一条保障。
紧急住宿、交通、维修若合用 1000，可在一个保障的 `covered_components` 列出三者，限额只写一条；不能自动变成三个各 1000。
这些例子用于说明开发文档中的表达方式，不是通用于所有公司的保额，也不表示已生成实际抽取结果。

天数、车龄、公里数、次数放 `quantities`，用 `metric/value/comparison/reference` 保存单位、大小关系与起算依据。
每天租车费和最多租几天不再混进同一个金额。`excesses` 单独存自付额，不当赔付上限；未提供金额不是 0。

## 3. 独立产品与可选保障分开

新增 `product_basis`（原文产品或命名档次）与 `product_identity_evidence`。
只有原文明确命名的独立产品/档次才建立产品记录，不能把勾选某项附加保障的假设组合当成新产品。

- QBE TPPD 中 Fire and theft cover option 按附加险处理，基础 `product_type` 仍为 TPPD。
- AAMI 明确区分的保障档次、Youi 明确列出的产品类型，可在各自原文证据支持下生成独立记录。
- `available_addons` 表示“可购买”，不是“客户已购买”；不接受 `selected` 字段。
- 可选保障通过 `option_id` 与附加险的 `benefit_ids` 双向对应。挂在附加险下的保障不能标成基础 `included`。

验证器能拒绝错误结构和引用，不能仅靠字段格式证明模型引用的原文真的支持该产品。产品边界仍须核对 PDF。

## 4. 嵌套约束进入实际抽取路径

`item_schema` 不再只是描述文本：编译器把它带进输出 contract，关闭额外键、明确必需键/枚举/数值类型。
抽取器启用完整类型结构的 strict 请求，并在收到结果后执行本地 JSON 校验和业务校验。
包括金额状态一致性、共享限额引用、可选项引用、重复 ID/规则、证据非空、表格列数与 `_unfilled`/null 一致性。
嵌套 schema 不允许远程引用。未声明该 profile 的旧 vertical 保持原来的输出布局。

离线测试使用合成结果及模拟 provider，覆盖有效输出与失败路径；没有测试真实模型的准确率或远端 API 对整份 contract 的接受情况。
本轮选定回归共 210 项通过，其中新增 car schema 修订测试 23 项；原稿 SHA-256 与来源记录一致，生成的 schema/contract 与当前代码一致。
即使通过所有检查，仍需复核内容是否忠实于原文，不能把格式正确等同于业务正确。

<a id="english-version-text"></a>

## English version

On 2026-10-03, four review areas were implemented as a candidate schema, extraction constraints and offline tests. This is neither human-approved nor benchmark gold. No model calls or holdout/test inspection occurred; the original model draft remains unchanged.

### Files and use
Under outputs/car_insurance/schema_trial_20261003, schema_draft.json retains the original envelope; schema_review_v2.json is a raw extractor-ready schema with 31 product fields, 25 executable nested definitions; extraction_contract_v2.json validates outputs, not discovery inputs; revision_v2_provenance.json records source SHA-256 and zero model calls. The builder is src/car_insurance/schema_revision.py; the local build_revision_v2.py refuses overwrite.

This intentionally changes structure: renaming old results does not migrate them. It is not the default schema; select it explicitly with --schema and retain parser-review fingerprint checks. Review first, then a small development extraction. Consensus or final evaluation is not started automatically. Structural/taxonomy changes must update the profile and tests together; new fields cannot be silently ignored.

### 1. Single ownership
Dedicated coverage fields own their benefits; other_coverages permits only other_documented_benefit. Business items/campervan contents belong to additional_item_benefits. available_addons replaces duplicate optional_benefits amounts with eligibility and benefit-ID references; amounts stay on benefits. policy_rules combines eligibility/use restrictions and general exclusions, distinguished by kind. PDF metadata and summary tables move outside products into document_evidence, stored once. issuer_legal_name and risk_underwriter_legal_name distinguish roles even if one entity fills both.

Each benefit retains ID, category, variant, source title and evidence. Repeated IDs or category/variant/option tuples fail. Paraphrased semantic duplicates still require human review; dedicated fields are retained for readability.

### 2. Amounts and quantities
Limits separate numeric AUD amount, amount_kind, period, basis, shared_with, conditions/source/evidence. Only fixed carries a number; unknown, unlimited, reasonable_costs and schedule_specific carry null. Per claim/incident/policy period/day is independent of aggregate/per-person/per-item scope. Shared caps are stored once and referenced.

Illustrative development expressions: counselling 1500/per_claim/per_person; funeral 5000/per_policy_period/aggregate. A combined emergency accommodation/transport/repair 1000 cap must not become three independent 1000 caps; covered_components may identify the components. These are representation examples, not universal insurer terms or generated results.

Days, vehicle age, kilometres and counts use quantities with metric/value/comparison/reference. Daily hire cost and maximum days are separate. Excesses are deductibles, not benefit caps; unspecified amounts are not zero.

### 3. Products versus add-ons
product_basis and product_identity_evidence require explicitly named products/tiers. Do not invent products for hypothetical add-on combinations. QBE Fire and theft remains an add-on to TPPD; AAMI/Youi named tiers may form separate source-supported records. available_addons means purchasable, not purchased; selected is forbidden. option_id and benefit_ids must agree bidirectionally; add-on benefits cannot be base included coverage. Structural checks do not prove that cited text supports the product boundary.

### 4. Executable nesting
item_schema compiles into closed output objects with explicit required keys, enums and numeric types. The extractor uses strict typed output plus local JSON and business validation: amount status, shared/optional references, duplicate IDs/rules, nonempty evidence, table column counts and _unfilled/null consistency. Remote nested-schema references are forbidden; older verticals without this profile retain their layouts.

Synthetic/mock-provider tests cover valid and invalid paths, not actual model accuracy or remote API acceptance. All 210 selected regressions passed, including 23 new car-schema tests. Draft hash/provenance and generated schema/contract match code. Content still needs review against the PDF.
