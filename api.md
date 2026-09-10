# API 与文件契约参考

依据 `main` 的 `62aaa7a` 核对，更新于 2026-09-10。本文描述 Python、CLI 和文件接口；项目没有 HTTP / REST API。操作步骤见 [实用手册](docs/user-guide.md)，模块职责见 [架构](docs/architecture.md)。

Python 示例从仓库根目录运行，使用 `.venv/bin/python`。下列接口是推荐集成入口，不代表已承诺长期版本兼容；升级时应保留 manifest、schema、审核产物和调用代码的版本。内部 adapter、UI session state 和数据库表细节不应成为业务调用方的接口。

## 1. 接口地图

| 需求 | 权威模块 | 网络 / 写入 |
| --- | --- | --- |
| 领域配置 | [`src.verticals.manifest`](src/verticals/manifest.py) | 本地读文件 |
| 模型选项 | [`src.common.model_config`](src/common/model_config.py) | 读环境 / 配置 |
| 抽样 / PDF 转文本 | [`src.schema.sampler`](src/schema/sampler.py)、[`src.PDFingestor.adapter`](src/PDFingestor/adapter.py) | 本地读 PDF，解析可写缓存 |
| 加载与统一 schema | [`src.schema.loader`](src/schema/loader.py)、[`src.schema.validation`](src/schema/validation.py) | 本地 / 内存 |
| 编译提取契约 | [`src.schema.contract`](src/schema/contract.py) | 内存 |
| 发现字段 / 生成 patch | [`src.schema.discovery`](src/schema/discovery.py) | 模型 API、缓存、usage / 失败诊断 |
| 按 schema 提取 | [`src.schema_application.extractor`](src/schema_application/extractor.py) | 模型 API、缓存、可写提取产物 |
| proposal 共识 | [`src.refine.consensus`](src/refine/consensus.py) | 模型 API、多个审核产物 |
| 人工决策与 Apply | [`src.refine.human_review`](src/refine/human_review/__init__.py) | 本地读写 |
| 质量分析 | [`src.schema_application.analyze`](src/schema_application/analyze.py) | 本地 / 内存 |
| Canonical 审批 | [`src.schema.canonical`](src/schema/canonical.py) | 内存 |
| PostgreSQL | [`src.storage.service`](src/storage/service.py) | 本地预检，建表 / 加载时连接 DB |
| 严格 JSON / 持久化 | [`src.common.json_codec`](src/common/json_codec.py)、[`src.common.json_artifacts`](src/common/json_artifacts.py) | 内存 / 本地读写 |

集成时显式传同一个 `VerticalManifest`，不要分别猜测 vertical、路径、cardinality 或 taxonomy。配置来自 `configs/*/manifest.json` 和包内三个 prompt；不使用旧的静态 vertical / prompt 列表。

## 2. Manifest 与模型选择

```python
from src.verticals.manifest import discover_manifests, resolve_manifest
from src.common.model_config import resolve_selection

manifests = discover_manifests()
manifest = resolve_manifest(
    "configs/travel_insurance/manifest.json", operation="extract"
)
selection = resolve_selection(
    provider="openai", model="gpt-5", document_input="markdown"
)
assert manifest.vertical == "travel_insurance"
assert manifest.documents.output_cardinality == "multiple"
```

此段离线可运行，不创建模型请求。

### `src.verticals.manifest`

| 接口 | 返回 / 行为 |
| --- | --- |
| `discover_manifests(config_root=None)` | `dict[str, VerticalManifest]`；扫描 `*/manifest.json`，校验配置并拒绝重复 code |
| `load_vertical_manifest(path)` | 校验后的 manifest，检查版本、资源路径和契约一致性 |
| `resolve_manifest(path=None, *, vertical=None, operation=None)` | 选择配置并检查操作能力；path 与 vertical 同时给出时必须一致 |
| `default_manifest_path(vertical)` | 从发现的配置包中返回 `Path`，不是硬编码映射 |

无显式选择时优先 Health；指定操作而默认领域不支持时，可选择唯一具备该能力的领域；多个候选无法唯一确定时失败。自动化脚本建议一直传 manifest。

`VerticalManifest` 是冻结数据类，常用属性和方法：

- `vertical`、`display_name`、`manifest_version`、`source_path`。
- `product_types`、`taxonomies`、`identity_fields`。
- `documents.categories`、`document_types`、`extraction_unit`、`output_cardinality`、`category_product_types`。
- `consensus_runs`、`promoted_decisions`、`manual_only_queue`、`protected_fields`。
- `supports(capability) -> bool` / `require_capability(capability) -> None`。
- `path(name) -> Path`、`contract(name) -> str`、`adapter(name) -> str`。
- `prompt(stage) -> str` 返回校验后的 **prompt 文件路径**，不是文本；文本可用 `src.verticals.registry.get_prompt(path)` 读取。

未配置的资源、错误领域、路径越界或不支持的操作抛出 `ManifestValidationError`（`ValueError` 子类）。adapter 仅限已有注册项，manifest 不执行任意 Python 代码。

### `src.common.model_config`

```text
ModelSelection(provider: str, model: str, document_input: str)
resolve_selection(*, provider=None, model=None, document_input=None,
                  environment=None) -> ModelSelection
resolve_api_key(selection, environment=None) -> (key | None, environment_name)
require_structured_output_capability(selection) -> StructuredOutputCapability
```

优先级：显式参数 → 进程环境 → 默认值。默认 `openai / gpt-5 / markdown`；非 OpenAI 需要模型名。变量为 `LLM_PROVIDER`、`LLM_MODEL`、`LLM_DOCUMENT_INPUT`，OpenAI 另有 `OPENAI_MODEL` 后备。凭证变量见 [手册](docs/user-guide.md#2-安装与环境配置)。不自动读取 `.env`，不得输出 `resolve_api_key()` 返回的 key。

`require_structured_output_capability()` 按 [model_capabilities.json](configs/model_capabilities.json) 检查 provider / 模型模式 / 输入方式，返回对象的 `.mode` 为结构化模式。`resolve_selection()` 本身不验证远程模型可用性。Discovery / extractor 的主流程只接受 `markdown`。

## 3. Schema 与提取数据模型

公共 discovered schema 的形状如下。这是形状示意，不是可提交的 JSON：

```text
{
  vertical, version, description,
  product_types: [产品类型字符串, ...],
  fields: [{name, type, description, applies_to, required, values}, ...],
  taxonomies: {分类集合名: [{canonical_name, description}, ...]},
  notes: [字符串, ...]
}
```

`product_type` 分类字段位于 `fields`；Travel 也不再使用单独的 `product_type_field`。taxonomy 键来自 manifest。字段类型限定为 `string / number / boolean / enum / list[object]`；字段名称 snake_case 且唯一，`applies_to` 引用本领域产品类型。没有新 aliases 或自动同义词归并。

```text
load_schema_data(path, manifest=None) -> dict
normalize_schema(payload, manifest=None) -> dict
validate_schema_mapping(payload, *, manifest=None) -> dict
compile_extraction_contract(schema, *, data_contract=None,
    business_validator=None, output_cardinality=None, manifest=None) -> dict
validate_extraction_record(schema, payload, *, manifest) -> None
```

- `load_schema_data` 接受独立 schema JSON 或成功的 discovered schema envelope。Canonical 走独立严格校验分支；是否必须 approved 由相应使用边界继续检查。
- `normalize_schema` 先按当前或支持的历史契约校验，再返回公共结构副本。历史 Health 顶层 taxonomy 和 Travel 独立 classifier / taxonomy 可读；不改写源文件。
- `validate_schema_mapping` 返回通过业务校验的规范化副本，检查 classifier、产品类型、taxonomy、身份字段等。不要对模型输出自行转换类型或补字段。
- `compile_extraction_contract` 返回 JSON Schema，不调用模型、不写文件。默认契约和输出数量来自 manifest；显式 cardinality 必须一致。
- `validate_extraction_record` 校验身份、适用性、多产品重复身份等业务约束，应与 JSON Schema 校验配合使用。

Health 单对象；Travel 为 `{"products": [...], "_document_notes": null}`，至少一个产品。每个产品对象必须包含全部 schema 字段以及 `_unfilled`、`_notes`，不允许额外顶层字段。discovered 字段允许 `null`；`required: true` 不等于编译后禁止空值，它也用于缺失分析。manifest 身份字段另受非空约束。`list[object]` 内部对象目前开放，不提供递归字段 DSL。

已知产品类型时，不适用字段不能填写非空值。多产品身份按去除首尾空白及大小写折叠后的值比较；原始返回值不因此改写。

### 完全离线的最小契约示例

这仅演示契约机制，不是用于真实保险提取的推荐字段集。

```python
from src.verticals.manifest import resolve_manifest
from src.schema.validation import validate_schema_mapping, validate_extraction_record
from src.schema.contract import compile_extraction_contract
from src.common.json_contracts import validate_inline_contract

manifest = resolve_manifest(vertical="private_health")
types = list(manifest.product_types)
schema = validate_schema_mapping({
    "vertical": manifest.vertical,
    "version": "example-v1",
    "description": "Offline contract example",
    "product_types": types,
    "fields": [{
        "name": "product_type",
        "type": "enum",
        "description": "Insurance product classification",
        "applies_to": types,
        "required": True,
        "values": types,
    }],
    "taxonomies": {name: [] for name in manifest.taxonomies},
    "notes": [],
}, manifest=manifest)
contract = compile_extraction_contract(schema, manifest=manifest)
record = {"product_type": "hospital", "_unfilled": [], "_notes": None}
validate_inline_contract(record, contract, "example_extraction")
validate_extraction_record(schema, record, manifest=manifest)
```

## 4. 采样与 PDFingestor

```text
select_samples(input_root: Path, categories=None, per_category=5,
               seed=None, exclude_paths=()) -> list[str]
category_from_path(path, categories=None) -> str | None
document_identity(path) -> str
```

位于 `src.schema.sampler`。每类取不同公司，以内容 hash 去重；`exclude_paths` 同时排除相同路径和内容。目录、公司或可用内容不足会抛 `ValueError`。显式传 `manifest.documents.categories`，避免 Travel 调用使用 Health 默认分类。

```text
render_pdf_paths_for_prompt(pdf_paths, *, cache_dir=None, pdf_root=None,
                            camelot_enabled=True) -> str
ingest_pdfs(pdf_paths, *, cache_dir=None, pdf_root=None,
            camelot_enabled=True) -> tuple[ParsedPDF, ...]
build_ingestor(cache_dir=None, *, camelot_enabled=True) -> PDFIngestor
```

位于 `src.PDFingestor.adapter`。`pdf_root` 解析尚未定位的相对路径；本地解析不调用 LLM，缓存身份包含 PDF 内容及解析配置。输出保留页、文本块和表格信息；Camelot 是可选回退。视觉提取需要显式注入 parser 的 `vision_page_extractor`，主流程不自动启用。

检查原始结构可用 `PDFIngestor.ingest(pdf_path, *, use_cache=True) -> ParsedPDF`，类型见 [`models.py`](src/PDFingestor/models.py)。文件缺失或解析失败可能产生文件 / 解析库异常，不保证统一异常类。

## 5. Discovery 与 Extraction

以下列推荐关键字参数；兼容及测试注入参数的完整签名以链接源码为准。

### `SchemaDiscovery`

构造：`SchemaDiscovery(manifest=..., selection=..., provider=None, usage_log_path=None, pdf_root=None, pdfingestor_cache_dir=None, timeout_seconds=600.0, log=print)`。

| 方法 | 返回 | 文件与失败语义 |
| --- | --- | --- |
| `discover(sample_pdfs, output_path=None, *, run_id=None)` | 校验并统一后的 schema `dict` | 本方法不保存成功 schema；`output_path` 用于诊断上下文，CLI 负责成功 envelope |
| `discover_patches(sample_pdfs, current_schema, output_path=None, *, run_id=None)` | 校验后的 patch set `dict` | 验证基础 schema 与 refinement capability，失败不能作为正常 patch 返回 |

显式用 `resolve_selection()` 构建 selection，避免构造器兼容默认值与进程环境产生歧义。`provider` 可注入实现 `ModelProvider` 协议的测试对象。推荐从 manifest 获取 prompt / contract，不在调用方重复领域规则。

### `SchemaExtractor`

构造：`SchemaExtractor(schema_data, manifest=..., selection=..., provider=None, usage_log_path=None, pdf_root=None, pdfingestor_cache_dir=None, timeout_seconds=600.0, log=print)`。

| 方法 | 返回 | 文件与失败语义 |
| --- | --- | --- |
| `extract_one(pdf_path, *, run_id=None)` | 提取内容 `dict` | 不保存成功文件；不是 `ExtractionResult`，也不是 envelope |
| `extract_many(pdf_paths, out_dir)` | 成功文件的 `list[Path]` | 逐份写 extraction envelope，重名另存；失败写诊断并抛异常，停止后续文档 |

接受公共 discovered schema 或 approved Canonical schema。模型返回需通过结构和业务校验。需要汇总费用时显式配置 `usage_log_path`。`extract_one` 会先检查给定路径是否存在，因此传绝对路径或相对当前目录可定位的路径；不要只靠 `pdf_root` 补全它。

下例会调用模型，需实际 PDF、凭证和费用预算；编写文档时未运行：

```python
from src.verticals.manifest import resolve_manifest
from src.common.model_config import resolve_selection
from src.schema.loader import load_schema_data
from src.schema_application.extractor import SchemaExtractor

manifest = resolve_manifest(vertical="travel_insurance", operation="extract")
schema = load_schema_data(manifest.path("canonical_schema"), manifest)
extractor = SchemaExtractor(
    schema_data=schema,
    manifest=manifest,
    selection=resolve_selection(),
    pdf_root=manifest.path("input_root"),
    usage_log_path=manifest.path("output_root") / "extraction_usage.jsonl",
)
data = extractor.extract_one(manifest.path("input_root") / "allianz/pds/example.pdf")
```

## 6. Provider 与有界结构修复

`src.common.model_provider`：

| 类型 / 函数 | 契约 |
| --- | --- |
| `ModelProvider` | 协议：`generate(request: ProviderRequest) -> ModelResponse` |
| `create_provider(selection, *, client=None)` | 选择 OpenAI / Anthropic / DeepSeek adapter，不表示已成功调用远端 |
| `StructuredOutputSpec(name, schema, strict=True)` | provider 中立的输出约束 |
| `ProviderRequest` | 必填 `selection, system_prompt, user_text, document_paths, timeout_seconds, cleanup_documents, request_params, background, poll_interval`；可选 `log=None, structured_output=None` |
| `ModelResponse` | `text, provider, model`；可选 `response_id, usage, api_key_env` |
| `ModelUsage` | `input_tokens, output_tokens, total_tokens`，均可为 `None` |

`src.common.structured_output`：

```text
run_structured_output(provider, request, *, data_contract=None,
    data_contract_schema=None, business_validator=None,
    max_repair_attempts=2) -> StructuredOutputResult
```

要求 `request.structured_output`，且两个 data contract 参数恰好一个。流程：严格 JSON → JSON Schema → 可选业务校验；默认最多初次请求加两次修复。返回 `data` 和逐次 `attempts`；attempt 包含序号、响应、校验错误。拒绝响应等错误可提前终止，网络异常不保证走结构修复。

修复耗尽抛 `StructuredOutputFailure`，其 `.result` 包含尝试与错误，不能把无效响应作为有效 data。transport / SDK 重试与结构修复不同，不能由“三次”推导固定费用上限。

## 7. Consensus 与人工审核

```text
SchemaConsensusRefinement(discovery, log=print, *, manifest=None)
  .refine(base_schema_path, input_root=None, categories=None,
          per_category=5, runs=None, seed=None, samples=None,
          base_sample_paths=(), output_dir=None,
          alias_config_path=None) -> ConsensusOutputs
```

`runs=None` 读取 manifest 默认值。调用模型并写 patches、consensus schema、频率、稳定性和审核队列。返回对象字段：`patch_dir, consensus_schema_path, frequency_path, report, stability_path, queue_path, decisions, schema_build_samples`；其中 `decisions` 是聚合的 `FieldDecision` 列表，不是人工决策文件。

按不同 proposal run 计票，默认 core ≥ 0.8、conditional ≥ 0.5、candidate ≥ 0.2，其余 noise；是否提升还取决于冲突、保护字段和 manifest 策略。Health 默认一次，Travel 五次。`alias_config_path` 仅为拒绝旧调用而保留，非空即报错。

### 审核文件接口

下表模块前缀均为 `src.refine.human_review`。

| 接口 | 行为 |
| --- | --- |
| `queue.load_review_queue(path, *, data_contract="schema_refinement/review_queue")` | 返回校验后的 queue data |
| `decisions.load_review_decisions(path)` | 返回决策 data |
| `decisions.empty_decisions(reviewer="", *, queue=None)` | 新集成应传 queue，绑定审核身份 |
| `decisions.save_review_decision(path, item_id, action, reviewer_notes="", edited_update=None)` | 推荐磁盘更新入口；读取相邻 queue、校验身份，锁内合并并保存，返回 data |
| `decisions.remove_review_decision(path, item_id)` | 删除该条决策，恢复 Pending，返回 data |
| `decisions.derive_status(queue, decisions_payload)` | 校验身份并返回 `dict[item_id, status]` |
| `apply.apply_review(queue, decisions_payload, base_schema, *, allowed_product_types=None)` | 内存应用，返回 `(reviewed_schema, summary)` |
| `apply.apply_review_files(consensus_dir, base_schema_path=None, output_path=None)` | 从关联文件加载并写审核产物，返回 `(Path, summary)` |

action 仅 `accept / reject / edit`；Edit 需要完整有效的 `edited_update` 字段定义。summary 含 `applied / edited / rejected / pending` 四组 item ID。Pending、Reject 不应用；rename / merge / move 不能伪装成字段 upsert，旧 add_alias 只能审计。

审核身份包含 queue ID、vertical、schema version、base schema hash。不要手工构造另一套身份或覆盖基础 schema。`write_review_decisions()` 是低层完整写入接口，交互式集成优先增量保存，避免旧内存副本覆盖新决策。

默认输出 `consensus/reviewed_schema.json`，已有文件拒绝覆盖。loop 的 `--resume-review` 固定读取该名，重新根据 queue / base / 当前 decisions 核对内容；另存的 `--out` 文件不会自动被 resume 采用。历史缺少身份绑定的队列不支持直接恢复。

## 8. 质量分析与稳定性

```text
load_field_specs(schema_data, manifest=None) -> list[FieldSpec]
load_records(extraction_dir: Path, extraction_contract, manifest=None,
             *, schema_version=None) -> (list[ExtractionRecord], failed_count)
analyze(records, specs, *, failed_artifacts=0) -> Analysis
print_report(analysis) -> None
build_feedback_data(analysis) -> dict
build_feedback(analysis) -> str
```

位于 `src.schema_application.analyze`。传入与提取一致的 discovered schema、manifest 和版本；`load_field_specs` 不接受 Canonical。`load_records` 支持 envelope / 主 CLI `ExtractionResult`，排除 `errors/`，拒绝来源分类不明或歧义文件；已提供的 vertical / schema version 必须匹配，历史缺少部分 provenance 的提取仍可读。

`ExtractionRecord` 含 `data / source_document / source_category`。多产品展开为多条记录。适用性按 manifest 可信类别映射确定，不使用模型分类作真值。Travel 无产品标签，分类准确率和特定产品字段指标为 N/A。`build_feedback_data` 只返回 data；CLI 的 `--feedback-out` 可持久化 envelope。Travel 未启用 loop evaluation / feedback 恢复，独立报告不等于完整闭环。

`src.stability.signature.signature_from_file(path, *, manifest=None) -> SchemaSignature` 提取字段契约、产品类型、所有 taxonomy 的语义签名；另有 `signature_from_text(text, label, *, manifest=None)` 和 `signature_from_artifact(artifact, label, *, manifest=None)`。

`src.stability.compare.compare(signatures, show_items) -> float` 打印结果并返回综合稳定性分数；跨 vertical 比较失败。稳定性不是值准确率。

## 9. Canonical、采集与 PostgreSQL

### Canonical

| `src.schema.canonical` 接口 | 返回 / 约束 |
| --- | --- |
| `build_canonical_candidate(payload, manifest)` | 从 discovered schema 构建 candidate 副本；要求 storage capability，以 manifest 内现有 Canonical 为模板 |
| `validate_canonical_schema(payload)` | 校验并返回副本，允许合法 candidate / approved 状态 |
| `approve_canonical_schema(payload, *, reviewer, rationale, reviewed_at=None)` | 返回含审批与内容绑定信息的 approved 副本；不写文件、不调用模型、不入库 |
| `require_approved_canonical_schema(payload)` | 校验批准状态和内容绑定，返回副本 |
| `compile_canonical_extraction_contract(payload)` | 从 approved 契约生成提取 JSON Schema |
| `validate_canonical_extraction_identities(schema_payload, extraction_payload)` | 校验产品容器与非空、唯一的产品名身份，返回原 extraction 对象；完整字段结构另用编译出的 JSON Schema 校验 |

身份字段的 storage 类型不兼容时拒绝自动构建 candidate；新增普通字段可映射到 JSONB。审批不是修改 `status` 字符串，内容变化后必须重新审批。现有 Canonical 契约保留的空 aliases 字段不代表恢复 aliases 功能。

### 采集

`src.scraper.travel.run_travel_acquisition(*, config_path, data_root, output_root, insurer_codes=(), include_archived=False, discovery_only=False, http_client=None, run_id=None) -> AcquisitionOutcome`。

访问配置公开来源并写 acquisition 产物，下载模式还写 PDF。`discovery_only=True` 仍有网络和 metadata 写入；`http_client` 用于测试注入。返回对象含 `artifact_path / data / pdf_paths`。通用流程通过 manifest 中已注册的 acquisition adapter 调用，不另建 crawler。

### 存储服务

```text
resolve_database_url(environment_name="KONKRD_DATABASE_URL") -> str
prepare_storage_load(*, manifest, schema_path, artifact_path,
                     insurer_code) -> PreparedStorageLoad
initialize_storage(*, database_url, manifest, schema_path) -> None
load_extraction_artifact(*, database_url, manifest, schema_path,
                        artifact_path, insurer_code) -> StorageLoadSummary
```

`prepare_storage_load` 读取 approved schema、提取 artifact 和来源 PDF，检查边界并编译 load plan，不连接数据库。原 PDF 必须存在且满足 manifest 输入路径、公司和文档类型目录约束。

`initialize_storage` 使用 PostgreSQL 事务创建缺失的核心与 vertical 表，不迁移已有表。`load_extraction_artifact` 先预检，再在一个事务中写入；重复身份需通过一致性校验，冲突不静默覆盖。成功返回 `run_id / document_id / schema_version_id / products_loaded / release_ids`。服务管理并释放 engine，不返回连接。SQLite 不支持。

## 10. JSON 文件边界

### 严格 JSON 与契约

`src.common.json_codec.loads_json()` 拒绝重复键、非 JSON 常量等无效输入，不接受 Markdown、YAML 或类型猜测；`dumps_json()` 用于输出。`src.common.json_contracts`：

```text
load_contract(name, *, manifest=None) -> dict
validate_contract(payload, name, *, manifest=None) -> Any
validate_inline_contract(payload, schema, name="inline") -> Any
```

两个 validate 函数成功时返回原 payload，失败抛 `ContractValidationError`。动态 discovered 契约应显式带对应 manifest；省略时会按契约名中的 vertical 查找配置。常用契约名通过 `manifest.contract("discovered_schema")` / `manifest.contract("candidate_patch_set")` 获取；共有审核契约位于 `schema_refinement/`。不要凭不可信 JSON 的字符串动态导入 validator。

Envelope 形状示意：

```text
{
  artifact_type, contract_version, status, created_at,
  provenance: {
    run_id, provider, model, document_input,
    source_documents: [...], source_artifacts: [...],
    vertical?, schema_version?
  },
  data, error
}
```

成功时 data 为校验后对象、error=null；失败时 data=null，error 含 code / message / details。时间为 UTC RFC 3339。`contract_version` 是产物数据契约版本，不是领域 schema 的 `data.version`。当前提取 / feedback 记录领域及 schema 身份，历史产物可能缺少这些附加 provenance 字段。

### 文件读写

```text
build_success_artifact(*, artifact_type, contract_version, data, provenance,
    data_contract=None, data_contract_schema=None, created_at=None) -> dict
write_artifact(path, artifact, *, data_contract=None,
    data_contract_schema=None, overwrite=False) -> Path
read_artifact(path, *, expected_type=None, data_contract=None,
    data_contract_schema=None) -> dict
write_text_output(path, text, *, overwrite=False) -> Path
next_available_path(path: Path, reserved: set[Path] | None = None) -> Path
```

构建 / 写成功 envelope 必须给恰好一个 data contract。动态契约先 `load_contract(..., manifest=manifest)`，再用 `data_contract_schema=` 传入。`read_artifact` 返回完整 envelope，拒绝 failed、无效 JSON / envelope、不符的 expected type；如需校验 data，须显式传契约，expected type 不代替内容校验。

写入原子化，默认拒绝覆盖。`next_available_path` 只选文件名、不预留路径。失败用 `build_failure_artifact(...)` 配合 `write_failure_artifact(output_root, stage, run_id, artifact)` 保存到 `errors/<stage>/`，不写成成功 schema。

**主 CLI `extract / batch` 使用 `src.models.ExtractionResult`。** 顶层为 `vertical, schema_version, source_path, extracted_at, provider, model, data, evidences, normalized_names, warnings`。用 `ExtractionResult.model_validate(payload)` 读取、`.write_json(path)` 保存，不能传给 `read_artifact`。wrapper 校验不代替 data 的实际 extraction contract 校验；兼容字段不表示当前执行了 aliases 合并。

## 11. CLI 参数速查

完整参数以各入口 `--help` 为准。manifest 是文件路径；只有部分主命令支持 `--vertical`，loop 不支持该参数。

| 入口 | 必填 | 常用可选参数 |
| --- | --- | --- |
| `src/run.py discover` | 无，但需可读样本 | `--manifest --samples --input-root --categories --per-category --seed --provider --model --document-input --timeout --output --usage-log` |
| `src/run.py extract` | `--pdf --schema` | `--manifest --output --provider --model` |
| `src/run.py batch` | `--schema` | `--manifest --vertical --input-root --evaluate --provider --model` |
| `src/run.py crawl` | 无，默认来自 manifest | `--manifest --vertical --config --data-root --output-root --insurer --include-archived --discovery-only` |
| `src/run.py canonical-compile` | `--schema --output-dir` | `--manifest` |
| `src/run.py storage-init` | 无，schema 默认来自 manifest | `--manifest --schema --database-url-env` |
| `src/run.py storage-load` | `--artifact --insurer-code` | `--manifest --schema --database-url-env` |
| `src/refine/loop.py` | 无 | `--manifest --input-root --out-dir --per-category --seed --eval-per-category --eval-seed --consensus-runs --review-ui --resume-review --resume-feedback --autonomous --rounds --provider --model --document-input --timeout` |
| `src/refine/consensus.py` | 无，基础 schema 默认来自 manifest 输出根目录 | `--manifest --base-schema --input-root --per-category --runs --seed --out-dir --provider --model --document-input --timeout` |
| `src/refine/review.py apply` | `--consensus-dir` | `--base-schema --out` |
| `src/schema_application/analyze.py` | `--schema --extractions` | `--manifest --feedback-out` |
| `src/stability/compare.py` | `--schemas ...` 或 `--dir` | `--manifest --show-items` |
| `src/stability/measure.py` | 无 | `--manifest --runs --input-root --per-category --seed --out-dir --provider --model --document-input --timeout --temperature --show-items` |
| `src/cost/estimate.py` | 无，默认读 manifest 输出日志 | `--manifest --log --model --input-rate --output-rate --project --vertical NAME=COUNT --extraction-input-tokens --extraction-output-tokens` |

`cost --vertical NAME=COUNT` 是投影工作量标签，不是 manifest 选择器。`extract / batch` 没有 `--document-input`，使用统一模型环境。`--no-fallback` 是废弃兼容参数，没有启发式提取；`discover --keep-uploaded-files` 是旧文件生命周期选项，主流程使用本地 PDFingestor 文本。

成功通常返回 0，失败非 0，argparse 参数错误通常为 2；没有统一稳定的 JSON 错误协议，部分入口会抛异常。程序集成需要结构化错误时优先 Python 接口。主 CLI batch 汇总逐文件错误；`SchemaExtractor.extract_many` 的失败即停语义不同。

## 12. 异常与验证范围

| 异常 / 情形 | 调用方处理 |
| --- | --- |
| `ManifestValidationError` | 修正配置、资源路径或能力选择 |
| `StrictJSONError` / `ContractValidationError` | 拒绝输入，检查字段路径，不自动补救为成功 |
| `ArtifactError` | 检查上游文件、状态、类型、契约 |
| `StructuredOutputFailure` | 停止依赖该结果的阶段，保留 usage / 脱敏诊断，修正后新开运行 |
| `FileExistsError` | 换新产物路径，不默认 overwrite |
| 其他 `ValueError` | 检查 schema、身份、审核绑定或 storage 预检 |
| provider / PDF / DB 异常 | 保留异常链，按边界处理，不一概当作“无内容” |

本文离线示例和 CLI 参数可在无凭证环境验证。真实模型输出质量、PDF 解析表现、网站可用性及 PostgreSQL 事务集成需要各自环境；本文不声称已运行这些外部流程。
