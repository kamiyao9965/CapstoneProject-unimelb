# PRD: Travel Insurance 官方文档采集与关联

## 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档状态 | Draft，等待业务与技术评审 |
| 版本 | 0.1.0 |
| 日期 | 2026-08-10 |
| Vertical | Australian Travel Insurance |
| 建议实施分支 | `feat/travel-insurance` |
| 建议代码基线 | `origin/merged-pipeline` |
| 本文范围 | 产品需求、采集边界、文档关联模型、验收标准与实施建议 |

## 1. 执行摘要

本项目计划在现有 Australian Private Health Insurance schema discovery
项目上增加 Travel Insurance vertical。第一阶段不直接解决报价、推荐或保单销售，
而是建立一条可审计、可重复运行的官方文档采集链路，从澳大利亚保险公司的公开官网发现并下载
Travel Insurance 的 PDS、SPDS、Policy Wording、Benefits Summary 和 Brochure，
然后把属于同一个产品版本的文档关联成完整的 `product_release` 文档包。

这一设计的核心不是“尽可能多地下载 PDF”，而是形成一套可信的数据资产：每份文件都能回答
从哪里发现、何时下载、是否为当前版本、适用于哪些产品、与哪份 PDS 相关，以及其内容是否发生过变化。
只有具备这些信息，后续 schema discovery、字段提取、产品比较和企业级数据服务才有可靠基础。

第一版建议覆盖 Allianz、Cover-More 和 Southern Cross Travel Insurance 三家公司的公开官方文档，
并支持 `international_single_trip`、`annual_multi_trip` 和 `domestic` 三类产品。
技术上采用配置驱动、静态页面优先、最多跟进一层文档中心页面的受控采集方式，避免建设难以维护的
通用互联网爬虫。

## 2. 背景与问题

### 2.1 当前项目背景

当前项目已经具备 PDF 文档处理、schema discovery、structured output、schema validation、
refinement、human review、holdout evaluation 和成本评估等能力，但现有 contract、采样类别和业务校验
主要面向 Australian Private Health Insurance。

Travel Insurance 与 Private Health Insurance 都以保险条款文档为主要信息来源，因此可以复用大量
通用能力；但 Travel Insurance 的产品分类、保障项目、文档版本关系和官网发布方式不同，不能仅通过
替换提示词或输入目录完成可靠迁移。

### 2.2 用户问题

企业用户需要比较不同 Travel Insurance 产品，但公开信息分散在不同公司的产品页、PDS 页面和 PDF 中：

- PDS 或 Policy Wording 是核心合同文档，但链接位置和命名不统一。
- SPDS 会修改既有 PDS；如果只保留基础 PDS，提取结果可能已经过期。
- Brochure 或 Benefits Summary 便于快速比较，但内容可能是摘要，不能替代 PDS。
- 同一份文档可能覆盖 Single Trip、Annual Multi-Trip 和 Domestic 多个产品。
- 官网常同时展示 current 和 archived 文档，单靠 PDF 文件名无法稳定判断版本。
- 同一 PDF 可能在多个页面重复出现，或者 URL 改变但内容完全相同。

如果不先解决文档来源、版本和关联问题，后续模型即使正确读取了 PDF，也可能在错误的产品版本上生成
结构化数据，产生“技术上成功、业务上错误”的结果。

### 2.3 为什么优先做 Travel Insurance

Travel Insurance 与当前项目具有较高的迁移复用度：主要输入仍是公开 PDF，产品保障项目也适合表示成
结构化 contract。相比需要大量个人信息才能报价的 Car Insurance，Travel Insurance 第一阶段可以在
不进入 quote flow、不处理个人数据的条件下获得较高价值的数据。

这符合“企业用户”和“低成本迁移”的业务方向：先建立可复用的官方文档与版本数据层，再逐步增加
产品比较、保障差异分析和推荐能力。

## 3. 产品目标

### 3.1 核心目标

1. 从配置的保险公司官方公开页面发现 Travel Insurance 当前产品文档。
   原因：官方来源可提供最强的出处证明，并降低错误、过期和第三方转载带来的风险。
2. 验证、下载并按内容哈希去重 PDF。
   原因：URL、文件名和页面结构都会变化，内容哈希才是稳定的文档身份。
3. 将 PDS、SPDS 和 Brochure/Benefits Summary 关联到同一个产品版本。
   原因：单份文件不能完整表达一个当前有效产品；尤其 SPDS 可能改变 PDS 的实际条款。
4. 为每次采集生成完整 provenance、状态和错误记录。
   原因：企业数据需要可追溯、可复现，也需要区分“没有文档”和“采集失败”。
5. 让采集结果能够作为现有 schema discovery 与 extraction pipeline 的输入。
   原因：避免形成第二套孤立的数据处理系统，控制 vertical 迁移成本。

### 3.2 成功定义

MVP 完成时应满足：

- 配置至少 3 家保险公司，并能独立运行，单家公司失败不影响其他公司。
- 对人工标注的 current PDS/SPDS/Brochure 集合达到至少 95% 的发现覆盖率。
- 所有保存文件都通过 PDF 类型、文件签名和大小校验。
- 对人工确认的文档关系达到至少 95% 的高置信度关联准确率。
- 第二次运行相同来源时不重复保存相同内容，并能报告新增、变化、失效和未变化文档。
- 每份文件都可以追溯到来源页面、发现 URL、最终 URL、抓取时间和内容哈希。
- 模糊版本或模糊关系进入 review queue，不被静默标记为 current。

覆盖率目标采用人工标注集合而不是“官网全部 PDF”作为分母，因为官网可能包含 claim form、FSG、隐私政策
和历史文件，这些并不是本产品要采集的核心产品文档。

## 4. 非目标

MVP 明确不包含：

- 不进入在线报价、购买、登录或客户门户流程。
- 不提交姓名、年龄、目的地、旅行日期或健康信息。
- 不采集个性化 premium，也不承诺价格比较。
- 不绕过验证码、访问控制、robots.txt 或网站技术限制。
- 不建设全网搜索引擎或无限递归 crawler。
- 不自动把低置信度文档关系升级为正式数据。
- 不把营销 Brochure 当成最终合同依据。
- 不公开再分发原始 PDF；商业使用和再分发范围需要单独法律确认。
- 不在 crawler 内调用 LLM。模型处理属于下游阶段，不能影响确定性的数据获取结果。

这些边界可以降低隐私、法律、运行成本和数据正确性风险，并让 MVP 专注验证最关键的官方文档数据层。

## 5. 假设与待确认前提

本文基于以下假设。评审中若任何假设不成立，应先更新 PRD，再进入实现：

1. 首期市场范围是 Australia，来源以澳大利亚官方产品页面为准。
2. 首期用途是内部研究和产品验证，不公开重新发布保险公司的完整 PDF。
3. 首期关注当前可销售产品；历史版本可记录，但不作为默认 schema discovery 输入。
4. 团队接受少量人工 review，以换取文档版本和关联关系的可靠性。
5. PDS/Policy Wording 是合同主文档；SPDS 是有效修改；Brochure 和 Benefits Summary 是辅助摘要。
6. `origin/merged-pipeline` 在实施前仍是团队认可的最新集成基线。
7. 新第三方依赖必须先经过依赖政策评审和用户批准。

## 6. 用户与使用场景

### 6.1 数据工程/研究人员

需要运行一次采集命令，获得经过验证的 PDF、manifest 和文档关系，而不必手动逐个访问保险公司网站。

### 6.2 Schema 研究人员

需要选择一组 current、彼此关联且来源明确的文档，用于发现 Travel Insurance 的结构化字段，避免旧版本和
重复文件污染样本。

### 6.3 产品或商业分析人员

需要知道每家公司提供哪些产品类型、当前适用哪份 PDS/SPDS，以及不同产品的 benefit 信息来自哪份文档。

### 6.4 审核人员

需要查看低置信度的版本和关联结果，依据来源页面、标题、生效日期和正文证据接受、修改或拒绝系统建议。

## 7. MVP 范围

### 7.1 初始公司

| Provider | 初始官方入口 | 纳入原因 |
| --- | --- | --- |
| Allianz | <https://www.allianz.com.au/travel-insurance.html> | 产品类别较完整，适合验证一份 PDS 覆盖多个计划的情况 |
| Cover-More | <https://www.covermore.com.au/pds> | 有明确的 PDS 文档入口，适合验证 current/previous 文档发现 |
| Southern Cross Travel Insurance | <https://scti.com.au/our-policies/comprehensive> | 产品页和 policy wording 结构清晰，适合验证多产品、多页面来源 |

Southern Cross 还提供 Annual Multi-Trip 和 Domestic 产品页，可作为同一 provider 下多入口页面的测试样本。
后续可加入 nib、1Cover 等公司，但必须先确认其官方域名、文档入口和使用条款。

### 7.2 产品类型

MVP 使用以下 canonical product types：

- `international_single_trip`
- `annual_multi_trip`
- `domestic`

暂缓加入 `inbound`, `medical_only`, `working_overseas`, `cruise_only` 等类型。原因是第一版需要先验证通用
文档关联机制；过早扩充 taxonomy 会提高分类歧义和人工标注成本。

Rental vehicle excess 作为 Travel Insurance benefit 存在，不建立独立 vertical 或 product type。

### 7.3 文档类型

| Canonical type | 业务角色 | MVP 处理 |
| --- | --- | --- |
| `pds` | 产品合同主体，包括明确作为 Policy Wording 发布的主文档 | 必须采集 |
| `spds` | 对指定 PDS 的补充或修改 | 必须采集并建立 `amends` 关系 |
| `benefit_summary` | 保障限额和计划差异摘要 | 必须采集，存在时关联到产品版本 |
| `brochure` | 产品营销或概览材料 | 必须采集，存在时关联到产品版本 |
| `tmd` | Target Market Determination | 可选采集，不进入 MVP 核心比较 |
| `fsg` | Financial Services Guide | 仅记录发现结果，默认不下载 |
| `claim_form` | 理赔表格 | 跳过 |
| `archive` | 历史版本 | 记录但默认不进入当前产品包 |

将 Benefit Summary 与 Brochure 分开，是因为前者通常更接近结构化权益表，后者可能是营销说明。两者都不能
覆盖 PDS 的法律角色，但对快速比较和字段发现有不同价值。

## 8. 核心概念与关联模型

### 8.1 为什么不能只保存三个文件列表

PDS、SPDS 和 Brochure 不是简单的一对一关系：

- 一个 PDS 可能覆盖多个 product types。
- 一个 SPDS 可能修改一份或多份 PDS。
- 一个 Benefits Summary 可能同时比较多个 plan。
- 一个新版 PDS 会取代旧 PDS，但旧 Brochure 不应自动迁移到新版本。

因此系统必须支持多对多关系，并通过中间实体 `product_release` 表示某家公司在某一时期有效的一组产品文档。

### 8.2 实体模型

```text
Provider
  └── Product Family
        └── Product Release
              ├── PDS / Policy Wording
              ├── SPDS
              ├── Benefits Summary
              └── Brochure

Document ── Document Relationship ── Document
    │
    └── Document Release Link ── Product Release
```

### 8.3 关系类型

| 来源 | 目标 | `relationship_type` | 含义 |
| --- | --- | --- | --- |
| SPDS | PDS | `amends` | SPDS 修改或补充目标 PDS |
| Brochure | PDS | `summarizes` | Brochure 概述目标 PDS 所对应产品 |
| Benefits Summary | PDS | `summarizes_benefits_of` | 权益摘要解释目标产品的保障和限额 |
| 新 PDS | 旧 PDS | `supersedes` | 新版 PDS 取代旧版 PDS |
| TMD | Product Release | `targets_market_for` | TMD 描述产品版本的目标市场 |

### 8.4 产品版本身份

`product_release_id` 应由 provider、规范化产品家族和基础 PDS 版本共同决定。例如：

```text
allianz:travel_insurance:2026-01-01:ab12cd34
```

其中日期来自明确生效日期；短哈希来自主 PDS 内容。加入哈希是为了区分同一生效日下内容不同的重新发布文件。

### 8.5 文档关系判定

关系识别按照证据强度依次使用：

1. PDF 正文明确写明修改、补充或适用的 PDS 名称和日期。
2. 官方页面明确把多份文件放在同一 current product/document 区域。
3. Provider、产品名称和适用 product types 一致。
4. 生效时间重叠且没有更新 PDS 取代关系。
5. URL、文件名和 anchor text 提供辅助线索。

正文的明确引用优先于 URL 和文件名，因为网站维护者可能修改路径或使用模糊文件名。

### 8.6 置信度与人工审核

| 置信度 | 处理方式 |
| --- | --- |
| `high` | 自动关联，但保留 evidence 和匹配规则 |
| `medium` | 写入 review queue，审核后才能进入 current 文档包 |
| `low` | 保持未关联，不进入下游产品级处理 |

任何自动关联都必须包含 `evidence`。不能只保存一个 confidence 数字，否则审核人员无法理解系统为什么建立关系。

## 9. 用户流程

### 9.1 配置来源

维护人员在 tracked source registry 中配置 provider、入口 URL、允许域名、产品提示词和文档提示词。

### 9.2 Dry run

运行 dry run 只发现页面和候选文档，不下载 PDF。使用者先检查候选数量、域名、current/archive 判断及排除原因。

### 9.3 正式采集

系统按 provider 独立处理来源页面，验证 robots 和 URL，受控跟进一次文档中心页面，验证并下载 PDF。

### 9.4 元数据与关系解析

系统读取页面上下文和 PDF 可提取文本，识别文档类型、产品类型、有效日期、版本状态和文档关系。

### 9.5 人工审核

审核人员处理 `needs_review` 项目。接受后进入当前产品文档包；拒绝后保留决定及理由，防止下一次运行重复提出。

### 9.6 下游使用

Schema discovery 只选择：

- `version_status = current`
- 关系已确认或为 high confidence
- PDF 校验成功
- 不与当前样本内容重复

## 10. 功能需求

### FR-01：配置驱动的来源注册表（P0）

系统必须通过配置增加或修改 provider，不要求为每家公司复制一套 crawler。

原因：公司页面结构会变化；把 URL、选择规则和提示词放在配置层可以降低维护成本，同时让通用安全和下载逻辑
保持一致。

建议配置字段：

```json
{
  "provider_code": "allianz",
  "display_name": "Allianz",
  "entry_urls": ["https://www.allianz.com.au/travel-insurance.html"],
  "allowed_domains": ["allianz.com.au", "www.allianz.com.au"],
  "max_follow_depth": 1,
  "max_pages": 20,
  "requests_per_second": 0.5,
  "follow_page_hints": ["policy information", "policy wording", "pds"],
  "include_document_hints": ["pds", "product disclosure", "supplementary", "benefits summary"],
  "exclude_document_hints": ["claim form", "privacy", "financial services guide"],
  "product_type_hints": {
    "annual multi-trip": "annual_multi_trip",
    "domestic": "domestic",
    "comprehensive": "international_single_trip"
  }
}
```

### FR-02：受控页面发现（P0）

系统必须静态 HTML 优先，从入口页提取候选链接；仅允许跟进同一官方 allowlist 域名内、符合文档页面提示词的
一层页面。不得进行无边界递归。

原因：保险公司通常通过“产品页 → policy documents 页面 → PDF”发布文档，一层跟进足以覆盖主要场景；
无限递归会进入报价、理赔、新闻和隐私页面，增加风险和噪音。

### FR-03：保留页面上下文（P0）

每个候选链接必须保存 anchor text、最近的 section heading、来源页面和页面上的日期文本。

原因：`Current documents`、`Previous documents` 等版本信息经常存在于链接周围，而不在 PDF 文件名中。
如果只保存 URL，下载后会丢失最可靠的版本证据之一。

### FR-04：URL 与访问安全（P0）

系统必须：

- 只访问 HTTPS。
- 请求前和重定向后都验证 allowlist。
- 禁止访问 loopback、private、link-local 和本地文件地址。
- 尊重 robots.txt，并使用清晰的 User-Agent。
- 设置连接、读取和总超时。
- 设置每 provider 的速率限制、最大页面数和最大候选数。

原因：URL 可能来自页面内容并发生重定向，这会形成 SSRF 或意外跨域风险；限速和硬上限也可以避免错误配置造成
过量请求。

### FR-05：候选文档分类（P0）

系统必须区分 `pds`、`spds`、`benefit_summary`、`brochure`、`tmd`、`fsg`、`claim_form` 和 `unknown`。
分类必须保存命中的规则和证据，不得只输出标签。

原因：不同文档的法律地位和下游用途不同。把所有 PDF 当成 brochure 会使下游模型错误地将营销摘要解释为完整条款。

### FR-06：PDF 验证与流式下载（P0）

系统必须：

- 以流式方式下载，默认最大 50 MiB。
- 同时检查响应 Content-Type 和文件头 `%PDF-`。
- 拒绝 HTML 错误页、空文件和超限文件。
- 在验证完成后再将文件移动到正式路径。
- 计算 SHA-256，并使用哈希作为稳定身份。

原因：扩展名为 `.pdf` 的链接可能返回登录页、错误页或重定向页面；先写临时文件再验证可以避免留下半文件和
污染数据集。

### FR-07：幂等、去重和内容变化检测（P0）

同一 SHA-256 内容只能保存一次，但可以保留多个来源 URL。相同 URL 若返回新哈希，应记录为新文档版本或
`content_changed` 事件，不能覆盖旧文件。

原因：按 URL 去重无法识别同一文件的多个链接，也无法保留“同一路径内容被替换”的重要版本变化。

### FR-08：版本状态识别（P0）

支持以下状态：

- `current`
- `archived`
- `superseded`
- `unknown`
- `needs_review`

系统不能只因文档出现在入口页就认定为 current。判断必须结合页面区域、有效日期、替代关系和正文信息。

原因：错误地把旧 PDS 作为 current 比漏掉一份文档风险更高，因此不确定时必须 fail closed。

### FR-09：文档关联和产品文档包（P0）

系统必须为 PDS、SPDS、Benefits Summary 和 Brochure 建立显式关系，并形成 `product_release`。
一份文档必须允许关联多个 product releases。

原因：Travel Insurance 的一份 PDS 经常覆盖多个计划；强制单产品目录会复制文件或丢失适用范围。

### FR-10：Manifest 与 provenance（P0）

每个候选和下载结果都必须产生记录，包括成功、重复、跳过和失败。建议字段：

```json
{
  "document_id": "sha256:...",
  "provider_code": "allianz",
  "document_type": "spds",
  "product_types": ["international_single_trip", "annual_multi_trip"],
  "title": "Supplementary Product Disclosure Statement",
  "source_page": "https://...",
  "discovered_url": "https://...",
  "final_url": "https://...",
  "section_heading": "Current policy documents",
  "effective_from": "2026-05-10",
  "effective_to": null,
  "version_status": "current",
  "sha256": "...",
  "content_type": "application/pdf",
  "size_bytes": 1234567,
  "downloaded_at": "2026-08-10T12:00:00Z",
  "retrieval_status": "downloaded",
  "crawler_version": "0.1.0"
}
```

原因：只有成功记录会掩盖 coverage gap；失败与跳过原因同样是数据质量的一部分。

### FR-11：结构化错误码（P0）

至少支持：

- `ROBOTS_DISALLOWED`
- `OFF_DOMAIN_URL`
- `OFF_DOMAIN_REDIRECT`
- `PRIVATE_NETWORK_TARGET`
- `HTTP_ERROR`
- `TIMEOUT`
- `SIZE_LIMIT_EXCEEDED`
- `NOT_PDF`
- `DUPLICATE_CONTENT`
- `NO_DOCUMENT_CANDIDATES`
- `AMBIGUOUS_DOCUMENT_TYPE`
- `AMBIGUOUS_VERSION`
- `AMBIGUOUS_RELATIONSHIP`

原因：稳定错误码适合自动统计、告警和测试；原始异常文字可以保留为 redacted detail，但不能作为唯一接口。

### FR-12：Provider 故障隔离（P0）

一个 provider 或一个 URL 失败不能终止整个采集任务。最终 run status 可为 `success`、`partial_success` 或 `failed`。

原因：官网临时不可用或页面改版很常见；故障隔离能让批量运行仍产生可用结果，同时明确暴露失败范围。

### FR-13：Dry run 与报告（P0）

Dry run 应显示预计访问页面、候选 PDF、分类、版本判断、排除项和警告，不写入 raw PDF。

原因：这是上线新 provider 配置前成本最低的安全检查，也便于业务人员验证规则是否抓到了正确文档。

### FR-14：可选动态页面适配器（P1）

只有在静态 HTML 和官方文档入口不能满足需求时，才允许为特定 provider 开启浏览器渲染适配器。

原因：Playwright 等浏览器依赖体积大、运行慢、容易受页面变化影响。静态优先符合低成本迁移目标；动态能力应是
明确授权、按 provider 启用的 fallback，而不是默认路径。

### FR-15：历史对比（P1）

系统应能比较相邻 run，输出 added、removed、content_changed、metadata_changed 和 unchanged。

原因：企业价值不仅来自一次性下载，还来自持续发现 PDS 更新和 SPDS 发布。

## 11. 非功能需求

### 11.1 正确性

- 不确定的 document type、版本和关系不得静默猜测。
- 所有自动分类和关联必须保留 evidence。
- 下游只能消费经过 contract validation 的 success artifacts。

### 11.2 可重复性

- 相同配置、相同页面快照和相同文件内容应产生相同 canonical 分类和关系结果。
- 时间、run ID 等非确定性 provenance 不得影响语义结果比较。

### 11.3 性能与资源上限

- 默认每 provider 每秒最多 0.5 个请求。
- 默认最多跟进一层、20 个 HTML 页面和 100 个 PDF 候选。
- 默认 PDF 上限 50 MiB。
- 并发必须按 provider 和域名限流，MVP 可先串行以降低复杂度。

这些默认值优先保护来源网站和任务可控性。后续可基于真实运行数据调整，而不是提前增加复杂并发。

### 11.4 可维护性

- Provider 差异优先放在配置或窄适配器中。
- 通用 URL、安全、下载、哈希、manifest 和错误逻辑不得复制。
- 采集模块与 schema discovery、LLM provider 和 PDF 内容提取保持清晰边界。

### 11.5 可审计性

- 原始来源 URL、最终 URL、时间、哈希、分类规则、关系证据和审核决定必须保留。
- 审核决定不可被下一次采集静默覆盖。

## 12. 建议系统流程

```text
Tracked source registry
  -> URL/robots/security validation
  -> static entry-page discovery
  -> bounded document-page follow
  -> candidate classification with page context
  -> streaming PDF validation/download
  -> SHA-256 identity and de-duplication
  -> PDF metadata/text inspection
  -> version resolution
  -> relationship resolution
  -> review queue for ambiguity
  -> current product-release bundles
  -> schema discovery / extraction pipeline
```

采集与下游模型处理分开运行。原因是网络失败、文件真实性、版本识别和模型输出错误属于不同故障域；分开后可以
独立重试、测试和审计。

## 13. 数据与文件结构

建议结构：

```text
configs/travel_insurance/
  sources.json

contracts/travel_insurance/
  acquisition_run.schema.json
  document_manifest.schema.json
  product_release.schema.json
  relationship_review.schema.json

data/travel_insurance/raw/PDFs/
  allianz/
    pds/
    spds/
    benefit_summary/
    brochure/
  cover_more/
  southern_cross/

outputs/travel_insurance/acquisition/
  <run_id>/
    run.json
    documents.jsonl
    product_releases.json
    review_queue.json
    change_report.json
    errors/
```

原始 PDF 和 runtime outputs 必须继续忽略，不进入 Git。Tracked source config 和 JSON contracts 应进入 Git，
因为它们定义可重复行为和公共数据契约。

文件路径建议使用：

```text
<provider>/<document_type>/<sha256-prefix>_<sanitized-title>.pdf
```

产品类型不作为唯一目录层级，因为一份文件可能适用于多个产品。适用范围应保存在 manifest 的数组字段中。

## 14. CLI 需求

建议入口：

```bash
.venv/bin/python -m src.acquisition.travel \
  --config configs/travel_insurance/sources.json \
  --dry-run
```

正式运行：

```bash
.venv/bin/python -m src.acquisition.travel \
  --config configs/travel_insurance/sources.json \
  --output-root outputs/travel_insurance/acquisition
```

单 provider 调试：

```bash
.venv/bin/python -m src.acquisition.travel \
  --config configs/travel_insurance/sources.json \
  --provider allianz \
  --dry-run
```

CLI 必须提供清晰的阶段进度和最终摘要，但不得打印 PDF 正文、API key 或不必要的完整错误页面。

本 PRD 不强制最终模块名称；实施前应结合团队认可的最新 architecture owner map 确认入口位置，避免与现有
`src/run.py` 的 schema discovery 职责混淆。

## 15. Product Release 数据契约示例

```json
{
  "product_release_id": "allianz:travel_insurance:2026-01-01:ab12cd34",
  "provider_code": "allianz",
  "product_family": "travel_insurance",
  "product_types": [
    "international_single_trip",
    "annual_multi_trip",
    "domestic"
  ],
  "effective_from": "2026-01-01",
  "effective_to": null,
  "status": "current",
  "documents": {
    "primary_pds": ["sha256:pds..."],
    "supplements": ["sha256:spds..."],
    "benefit_summaries": ["sha256:benefits..."],
    "brochures": ["sha256:brochure..."]
  },
  "relationship_ids": ["relationship:..."],
  "association_status": "confirmed",
  "evidence": [
    "Documents appear in the same official current-policy section",
    "SPDS explicitly references the base PDS effective date"
  ]
}
```

`primary_pds` 使用数组而不是单值，是为了兼容一个产品包由多个主条款文件共同组成的情况；MVP 通常只有一个，
但 contract 不应提前排除合理的多文档产品结构。

## 16. Review Queue

Review item 至少包含：

- 候选 document 或 relationship ID。
- 系统建议及 confidence。
- 来源页面和最终 PDF URL。
- 页面 section heading、anchor text 和日期上下文。
- PDF 标题、有效日期和正文证据片段。
- 冲突信息，例如“页面标记 current，但存在日期更新的 PDS”。
- 可选决定：accept、reject、edit。
- 审核人、时间和理由。

审核队列本身在生成后应保持不可变，决定单独保存。原因是覆盖原队列会破坏审计轨迹，也无法区分系统最初建议和
人工最终决定。

## 17. 安全、合规与法律边界

### 17.1 网站访问

- 只访问配置的官方公开 URL。
- 尊重 robots.txt、合理限速和网站响应。
- 不绕过访问控制或隐藏自动化身份。
- 不访问 quote、account、claims submission 等交互式用户流程。

### 17.2 内容使用

公开可下载不等于允许商业再发布。例如 Allianz 网站另有 Terms of Use：
<https://www.allianz.com.au/terms-of-use.html>。

MVP 默认将 PDF 视为内部研究输入。进入企业产品、客户交付或公开数据集前，必须由业务和法律负责人确认：

- 是否允许自动下载。
- 是否允许长期保存。
- 是否允许提取事实数据。
- 是否允许向第三方展示原文、截图或完整 PDF。
- 是否需要保留版权声明或来源链接。

### 17.3 数据安全

系统不得采集个人信息，也不得将环境变量、凭据、PDF 正文或原始错误响应写入普通日志。

## 18. 依赖策略

MVP 应优先使用现有依赖和 Python 标准库。任何新增依赖都必须先更新 dependency policy 并获得批准。

潜在依赖决策：

- 静态 HTML：先验证标准库或现有能力是否足够。
- 更稳健的 HTML 选择器：httpx/BeautifulSoup 需要作为显式依赖评审，不能依赖间接安装。
- 动态页面：Playwright 仅在静态方式确实不能覆盖目标 provider 时考虑，且必须单独批准。
- PDF 元数据和正文：优先复用集成基线已有的 PDFingestor/PDF parsing 能力，不再增加平行 parser。

这一路径符合低成本迁移原则，同时避免因为“爬虫常用”就引入重型浏览器运行时。

## 19. 测试策略

### 19.1 单元测试

覆盖：

- URL 规范化、域名和 private IP 拒绝。
- 链接提示词和 document type 分类。
- 日期解析与 current/archive 判断。
- SHA-256 去重和文件命名。
- PDS/SPDS/Brochure 关联规则。
- 关系 confidence 和 review threshold。
- 结构化错误码。

### 19.2 Fixture 集成测试

使用保存的、经过最小化处理的 HTML fixtures 和伪造 PDF bytes，测试：

- 入口页直接链接 PDF。
- 入口页链接文档中心，再链接 PDF。
- current 与 archived 位于不同 section。
- 同一 PDF 有多个 URL。
- 同一 URL 内容发生变化。
- 重定向到非 allowlist 域名。
- `.pdf` URL 返回 HTML。
- 单 provider 失败后的 partial success。

Fixture 测试默认离线，避免 CI 依赖外部网站结构和网络稳定性。

### 19.3 Contract 测试

所有 acquisition run、manifest、product release、relationship 和 review artifacts 都必须通过权威 JSON Schema。
未知字段、非法枚举、缺少 provenance 和无 evidence 的自动关系应被拒绝。

### 19.4 Golden dataset 测试

为 3 家 MVP provider 建立人工确认的小型 golden dataset，至少标注：

- 应发现的 current 文档。
- 应排除的 archived/FSG/claim form。
- 正确 document type。
- 正确 product types。
- 正确 PDS/SPDS/Brochure 关系。

Golden dataset 用于衡量前述 95% 覆盖率和关系准确率。

### 19.5 Live smoke test

Live test 必须显式 opt-in，并只访问少量官方页面。通过标准包括：

- robots 和 allowlist 检查成功。
- 至少发现一个有效候选或给出可解释的 `NO_DOCUMENT_CANDIDATES`。
- 不访问配置外域名。
- 不进行报价或提交表单。

离线测试通过不能被描述为“官网采集已经验证”；只有实际运行 live smoke 才能做这一声明。

### 19.6 仓库级验证

实现阶段至少运行：

```bash
.venv/bin/python -m compileall src tests
.venv/bin/python -m unittest discover -s tests
```

如果新增 CLI，还必须运行其 `--help`。

## 20. 监控与运行报告

每次 run 至少汇总：

- Provider 总数和 success/partial/failed 数量。
- 访问页面数、候选数、下载数、重复数、跳过数和失败数。
- 按 document type 的数量。
- current、archived、unknown 和 needs_review 数量。
- 新增、变化、移除和未变化文档数。
- 自动关联、高/中/低 confidence 关系数。
- 每类错误码数量。
- 总下载字节和运行时长。

这些指标同时服务运行诊断和产品 KPI。例如候选数量突然归零通常表示页面改版，而不是该公司不再销售产品。

## 21. 分阶段实施建议

### Phase 0：需求和法律确认

- 评审本 PRD。
- 确认 MVP provider 和内部使用边界。
- 确认动态浏览器和新增依赖是否允许。

完成原因：访问和内容使用边界会直接影响实现，不应在代码完成后再补决定。

### Phase 1：采集 contract 与静态核心

- 建立 source registry、artifact contracts 和错误码。
- 实现 URL 安全、robots、静态发现、流式下载、PDF 验证、哈希和 manifest。
- 完成纯离线 fixture tests。

完成标准：不依赖任何单独 provider 规则即可在 fixture 上跑通端到端 acquisition。

### Phase 2：三家 Provider 配置

- Allianz。
- Cover-More。
- Southern Cross Travel Insurance。
- 对每家建立 golden expectations 和 opt-in live smoke。

完成标准：达到 current 核心文档 95% 发现覆盖率。

### Phase 3：文档版本与关联

- 提取标题和有效日期。
- 建立 product release、relationship resolver 和 review queue。
- 验证 PDS/SPDS/Brochure 多对多场景。

完成标准：人工标注关系上的 high-confidence precision 至少 95%。

### Phase 4：接入现有 pipeline

- 让 schema sampler 读取已确认的 current product releases。
- 增加 Travel Insurance contract 和业务校验。
- 验证 discovery、extraction、analysis 和 refinement 的 vertical 隔离。

完成标准：Private Health 默认行为不变，Travel 可以通过显式 vertical/config 运行。

### Phase 5：持续更新和可选动态页面

- Run-to-run change report。
- 页面改版告警。
- 仅为已证明需要的 provider 增加动态适配器。

## 22. MVP 验收标准

### 数据采集

- [ ] 至少 3 家 provider 配置通过 contract validation。
- [ ] 只访问 HTTPS 和 allowlist 域名，重定向后再次校验。
- [ ] 跟进深度和页面数量存在硬上限。
- [ ] 所有已保存文件均通过 PDF 签名和大小校验。
- [ ] 相同 SHA-256 不重复保存。
- [ ] 单 provider 故障产生 partial success，而不是丢失其他结果。

### 文档分类与关联

- [ ] PDS、SPDS、Benefits Summary 和 Brochure 被区分处理。
- [ ] SPDS 可以关联一份或多份 PDS。
- [ ] 一份文档可以适用于多个 product types。
- [ ] current/archive 判断保留页面和正文 evidence。
- [ ] 模糊关系进入 review queue。
- [ ] 已确认文档组成可验证的 product release artifact。

### 可审计性

- [ ] 每个候选都有 source page、discovered URL、final URL 和处理状态。
- [ ] 每个保存文件都有 SHA-256、时间、大小和 Content-Type。
- [ ] 失败和跳过使用稳定错误码。
- [ ] 审核决定与原始 queue 分开保存。

### 工程质量

- [ ] 不提交 raw PDFs、runtime outputs、usage logs 或 credentials。
- [ ] 不增加未经批准的依赖。
- [ ] clean checkout 下 compileall 和全部 offline tests 通过。
- [ ] CLI `--help` 可运行且不需要网络。
- [ ] README、architecture 和 project index 在实现公共 workflow 后同步更新。

## 23. 风险与缓解措施

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 官网页面改版 | 候选数量归零或分类失败 | 配置驱动、fixture、候选数量监控、provider 隔离 |
| Current/archived 误判 | 下游使用过期条款 | 页面上下文 + PDF 日期 + supersedes 关系；不确定则 review |
| SPDS 未关联 | 实际条款不完整 | 明确 `amends` 关系和 current bundle 完整性检查 |
| Brochure 与 PDS 内容冲突 | 比较结果不可信 | PDS/SPDS 优先级高于营销摘要，并保存来源级 evidence |
| 动态页面增加维护成本 | 本地和 CI 运行变重 | 静态优先，动态能力按 provider 显式开启 |
| 网站条款限制商业使用 | 企业发布存在法律风险 | MVP 内部使用；上线前完成 provider 级法律审核 |
| URL 重定向到非官方站点 | 安全和来源风险 | 请求前后 allowlist 与网络地址验证 |
| 现有 private-health 假设泄漏 | Travel 分类和评估错误 | vertical-specific contracts、taxonomy 和业务校验 |
| 新分支基线继续变化 | 合并冲突 | 短周期、小切片实施；在集成基线合并后及时 rebase |

## 24. 尚待产品评审的问题

1. 原始 PDF 的允许使用范围是内部研究，还是未来需要向客户展示或下载？
2. Archived PDS 是否需要完整保存，还是只记录 metadata 和 URL？
3. TMD 是否进入第一版下载范围？
4. MVP 是否确认 Allianz、Cover-More、Southern Cross，还是需要替换其中一家？
5. 关系 review 由谁负责，期望的响应时间是多少？
6. 采集运行频率是手动、每周还是每月？
7. 当官网 PDF 和页面摘要冲突时，是否统一采用 PDS/SPDS 为最高权威来源？
8. 是否允许新增 HTML parsing 依赖；如果不允许，标准库实现的维护成本是否可接受？
9. 是否需要在 MVP 中生成字段级 provenance，指出每个提取值来自 PDS、SPDS 还是 Brochure？

## 25. 外部参考

- Australian Government Moneysmart, Travel insurance:
  <https://moneysmart.gov.au/other-types-of-insurance/travel-insurance>
- Allianz Travel Insurance:
  <https://www.allianz.com.au/travel-insurance.html>
- Allianz Policy Information:
  <https://www.allianz.com.au/my-allianz/policy-information.html>
- Cover-More Product Disclosure Statements:
  <https://www.covermore.com.au/pds>
- Southern Cross Comprehensive Travel Insurance:
  <https://scti.com.au/our-policies/comprehensive>
- Southern Cross Annual Multi-Trip Travel Insurance:
  <https://scti.com.au/our-policies/annual-multi-trip>
- Southern Cross Domestic Policy Wording:
  <https://scti.com.au/our-policies/domestic/policy-wording>

外部页面会随时间变化。实施 provider 配置和 live smoke test 时应重新核对页面、robots.txt 和使用条款，不能把
本文中的 URL 视为永久稳定接口。

## 26. 建议决策

建议批准以下方向进入技术设计阶段：

1. MVP 先做三家 provider、三类 product types 和四类核心文档。
2. 使用 `product_release` 统一关联 PDS、SPDS、Benefits Summary 和 Brochure。
3. 使用官方页面、静态优先、最多一层跟进、严格 allowlist 的受控采集。
4. 低置信度版本或关系必须人工审核，PDS/SPDS 权威级别高于 Brochure。
5. 采集与 LLM/schema discovery 解耦，以 manifest 和 versioned JSON contracts 连接。
6. 实施分支建议使用 `feat/travel-insurance`，代码基线建议使用 `origin/merged-pipeline`。

本文获批前不进入实现阶段。评审后如业务范围、provider、法律边界或文档 taxonomy 有变化，应先更新本 PRD。
