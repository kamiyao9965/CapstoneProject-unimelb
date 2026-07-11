# API.md

本文档用于解释当前项目的业务主线、代码结构、模块调用关系、启动方式、参数传递和关键函数。  
这个项目不是网络服务，没有 HTTP 接口；这里的 API 指命令行入口、Python 模块边界和内部函数接口。

## 1. 当前业务逻辑总览

当前业务主线只有两步：

```text
1. 生成 schema
2. refine schema
```

更完整地说：

```text
PDF 文档
  -> 生成初版 schema
  -> refine schema
       -> 生成候选 draft
       -> 可选：字段共识（consensus）
       -> 可选：人工审核（human review）
       -> 留出样本 extraction evaluation
       -> feedback 进入下一轮
  -> 得到更稳定、更可抽取的 schema
```

### 1.1 第一步：生成 schema

入口：

```bash
.venv/bin/python src/run.py
```

业务含义：

```text
PDF 样本
  -> OpenAI
  -> YAML schema
  -> outputs/private_health/schema.yaml
```

这一阶段只做一件事：从 PDF 样本中生成一个 baseline schema。  
如果输出路径已经存在，程序会自动写到 `schema_1.yaml`、`schema_2.yaml` 等下一个可用文件。

### 1.2 第二步：refine schema

入口：

```bash
.venv/bin/python src/refine/loop.py
```

业务含义：

```text
当前 schema / 上一轮 feedback
  -> 生成 schema draft
  -> 可选 consensus
  -> 可选 human review
  -> 用 holdout PDFs 测 extraction 效果
  -> 生成 feedback.txt
```

这一阶段不是重新发明一个独立流程，而是围绕 baseline schema 做质量提升。

### 1.3 字段共识（consensus）在当前场景下做什么

consensus 是 refine 内部的字段稳定化步骤，不是单独的业务主线。

它解决的问题是：LLM 多次看类似 PDF 时，可能会提出相似但名字不同的字段。

例如：

```text
run 1: annual_limit
run 2: yearly_limit
run 3: annual_benefit_limit
```

这些可能其实是同一个字段。consensus 会：

```text
同一个 base schema
  -> 跑 N 次 patch generation
  -> normalize 字段名和 group 名
  -> 统计每个字段出现几次
  -> 判断字段是 core / conditional / candidate / noise
```

所以 consensus 回答的问题是：

```text
这个字段是不是稳定地被模型认为应该进入 schema？
```

它评价的是 schema 字段的内部稳定性。  
真正评价任务效果的是后面的 extraction evaluation：

```text
schema
  -> extract holdout PDFs
  -> analyze fill rate / required missing / enum violations
  -> feedback.txt
```

两者关系：

```text
consensus           = 字段稳定性：多次生成时是否稳定出现
extraction evaluate = 任务有效性：字段是否真的能从 PDF 抽出来
```

### 1.4 人工审核（human review）在当前场景下做什么

human review 也是 refine 内部的一步。  
当 `--review-ui` 开启时，程序不会直接使用自动合并结果，而是生成一个 `review_queue.yaml`，让人决定每个字段更新：

```text
accept -> 应用
reject -> 不应用
edit   -> 人工修改后应用
pending -> 不应用
```

这一步的目标是避免模型自动把不合适的 schema update 带入后续 extraction pipeline。

### 1.5 辅助工具不是主业务线

项目里还有一些可以单独运行的工具：

```text
src/refine/consensus.py
src/refine/review.py
src/extract/analyze.py
src/stability/compare.py
src/stability/measure.py
src/cost/estimate.py
```

它们不是新的业务主线，而是为了调试、复用、离线分析或成本估算，把主流程里的某些步骤拆出来单独运行。

## 2. 环境和依赖

依赖写在 `requirements.txt`：

```text
openai>=1.99.0
PyYAML>=6.0
streamlit>=1.37
```

推荐使用项目虚拟环境：

```bash
.venv/bin/python -m pip install -r requirements.txt
```

OpenAI API key 读取顺序：

```text
MY_OPENAI_API_KEY
OPENAI_API_KEY
```

`src/run.py` 的默认模型可以从 `OPENAI_MODEL` 读取。其他 CLI 默认模型目前是 `gpt-5`。

## 3. 输入和输出

默认输入 PDF 根目录：

```text
data/private_health/raw/PDFs/
```

采样器会在路径里查找这些类别：

```text
combined
extras
generalhealth
hospital
```

默认输出根目录：

```text
outputs/private_health/
```

常见输出：

```text
outputs/private_health/schema.yaml
outputs/private_health/token_usage.jsonl
outputs/private_health/consensus/
outputs/private_health/stability/
outputs/private_health/refine/round_N/
```

不要提交这些内容：

```text
.env
data/private_health/raw/PDFs/
outputs/
token_usage.jsonl
extraction_usage.jsonl
```

## 4. 顶层模块地图

这一节是理解项目结构的重点。树里的每个节点都说明它负责什么。

```text
CapstoneProject-unimelb/
├── API.md
│   └── 当前项目的业务逻辑、模块调用和运行方式说明。
│
├── requirements.txt
│   └── Python 依赖：openai、PyYAML、streamlit。
│
├── configs/
│   └── private_health/
│       └── aliases.yaml
│           └── private health 垂直领域的字段名和 group 名 alias 配置。
│
├── src/
│   ├── run.py
│   │   └── 第一步业务入口：从 PDF 样本生成 baseline schema。
│   │
│   ├── review_app.py
│   │   └── Streamlit UI 入口，真正 UI 实现在 refine/human_review/ui.py。
│   │
│   ├── common/
│   │   └── openai_run.py
│   │       └── OpenAI Responses API background polling 统一封装。
│   │
│   ├── schema/
│   │   ├── discovery.py
│   │   │   └── OpenAI schema / patch generation、PDF 上传删除、token log。
│   │   ├── prompts.py
│   │   │   └── schema discovery 和 patch discovery 的提示词。
│   │   └── sampler.py
│   │       └── 从 PDF 目录按 category 和 company 做平衡采样。
│   │
│   ├── refine/
│   │   ├── loop.py
│   │   │   └── 第二步业务入口：refine schema 的 CLI 兼容入口。
│   │   ├── consensus.py
│   │   │   └── 多轮 candidate patch 生成、normalize、投票和产物渲染。
│   │   ├── review.py
│   │   │   └── 离线应用 human review decisions 的 CLI。
│   │   │
│   │   ├── pipeline/
│   │   │   ├── cli.py
│   │   │   │   └── refine loop 参数解析和模式分派。
│   │   │   ├── rounds.py
│   │   │   │   └── round_N 状态机、consensus 暂停、resume-review。
│   │   │   └── steps.py
│   │   │       └── generate、consensus、evaluate 三个会调用 OpenAI API 的步骤。
│   │   │
│   │   ├── candidates/
│   │   │   ├── patch.py
│   │   │   │   └── SchemaPatch 数据模型和 YAML IO。
│   │   │   ├── normalizer.py
│   │   │   │   └── 字段名和 group 名 canonicalization。
│   │   │   ├── aggregator.py
│   │   │   │   └── 多轮 patch frequency voting，输出 FieldDecision。
│   │   │   └── stability.py
│   │   │       └── 基于同一批 patch runs 计算字段 drift。
│   │   │
│   │   ├── artifacts/
│   │   │   ├── renderer.py
│   │   │   │   └── 渲染 consensus_schema、field_frequency、report。
│   │   │   └── schema_fields.py
│   │   │       └── schema field upsert 和 applies_to 清洗。
│   │   │
│   │   └── human_review/
│   │       ├── constants.py
│   │       │   └── review artifact 文件名和支持的 action。
│   │       ├── queue.py
│   │       │   └── 构建不可变 review_queue.yaml。
│   │       ├── decisions.py
│   │       │   └── 读写 review_decisions.yaml，派生 item 状态。
│   │       ├── apply.py
│   │       │   └── 把 accepted / edited 更新应用到 base schema。
│   │       └── ui.py
│   │           └── Streamlit review UI 的页面和交互逻辑。
│   │
│   ├── extract/
│   │   ├── extractor.py
│   │   │   └── 用 schema 从 PDF 抽取 JSON records。
│   │   ├── analyze.py
│   │   │   └── 分析 extraction JSON，生成 failure feedback。
│   │   └── prompts.py
│   │       └── extraction prompt。
│   │
│   ├── stability/
│   │   ├── signature.py
│   │   │   └── 把 schema YAML 转成可比较的 signature。
│   │   ├── compare.py
│   │   │   └── 离线比较多个 schema 的 drift。
│   │   └── measure.py
│   │       └── 调用 OpenAI API 重复生成 schema 并测稳定性。
│   │
│   └── cost/
│       ├── pricing.py
│       │   └── 模型价格表和成本计算函数。
│       └── estimate.py
│           └── 从 token usage logs 估算运行成本。
│
└── tests/
    └── 离线 unittest，覆盖 consensus、review、pipeline 等纯逻辑。
```

## 5. 整体调用关系和逐文件说明

这一节按 `.py` 文件解释：这个文件整体做什么、被谁调用、里面的函数做什么。

### 5.1 `src/run.py`

整体作用：第一步业务入口，从 PDF 生成 baseline schema。

被谁调用：用户在命令行直接运行。

调用链：

```text
main()
  -> build_parser()
  -> select_samples() / 使用 --samples
  -> print_samples()
  -> SchemaDiscovery.discover()
  -> next_available_path()
  -> 写 schema YAML
```

函数说明：

- `build_parser()`：定义 `src/run.py` 的 CLI 参数。
- `main()`：执行 one-shot discovery 主流程。
- `next_available_path(path)`：如果输出文件已存在，生成下一个不冲突路径。

### 5.2 `src/schema/sampler.py`

整体作用：根据 PDF 路径中的 category 和 company 做平衡采样，并用
SHA-256 内容 identity 防止重复 PDF 跨 build/holdout 泄漏。

被谁调用：

- `src/run.py`
- `src/refine/pipeline/steps.py`
- `src/refine/consensus.py`
- `src/stability/measure.py`

函数说明：

- `select_samples(input_root, categories, per_category, seed, exclude_paths)`：每个 category 选择不同 company 和不同内容 identity，并排除建模阶段使用过的整个重复内容组。
- `document_identity(path)`：计算带文件元数据缓存的 SHA-256 identity。
- `collect_candidates(input_root, categories)`：扫描目录，按 category -> company -> PDF 建索引。
- `company_from_path(pdf_path, input_root, category)`：从 PDF 路径推断 company 名称。
- `print_samples(sample_paths, input_root, categories)`：把采样结果按 category 打印出来。

### 5.3 `src/schema/discovery.py`

整体作用：provider-neutral schema generation 和 patch generation 的 owner。负责 schema/patch 请求内容、YAML 清洗和 schema-specific usage payload；完整 schema 在记录成功和返回前由 `src/schema/validation.py` 验证。

被谁调用：

- `src/run.py`
- `src/refine/pipeline/steps.py`
- `src/refine/consensus.py`
- `src/stability/measure.py`

核心类：

- `SchemaDiscovery`：封装 schema discovery 和 patch discovery。

核心方法：

- `discover(sample_pdfs, output_path=None)`：用 PDF 样本生成完整 schema YAML。
- `discover_patches(sample_pdfs, current_schema, output_path=None)`：基于当前 schema 生成 candidate patch YAML。
- `_generate_from_pdfs(...)`：公共内部实现，通过 shared OpenAI lifecycle 调用模型并记录 usage。
- `_input_content(pdf_paths, file_ids)`：构造完整 schema discovery 的 user content。
- `_patch_input_content(pdf_paths, file_ids, current_schema)`：构造 patch discovery 的 user content。
- `_log_usage(...)`：把 token、耗时、样本等写入 JSONL。

### 5.3.1 `src/schema/validation.py`

整体作用：验证完整 private-health schema 和单个 field payload；拒绝无效
YAML、重复字段、未知 product type、缺失 required/applies_to，以及没有
allowed values 的 enum。

### 5.4 `src/schema/prompts.py`

整体作用：集中保存模型提示词。

被谁调用：

- `src/schema/discovery.py`

内容说明：

- `SCHEMA_DISCOVERY_PROMPT`：要求模型从 PDF 生成 production schema YAML。
- `SCHEMA_PATCH_PROMPT`：要求模型基于现有 schema 生成 candidate patches。

### 5.5 `src/common/openai_run.py`

整体作用：项目唯一的共享 OpenAI runtime lifecycle owner。

被谁调用：

- `src/schema/discovery.py`
- `src/extract/extractor.py`

函数说明：

- `resolve_api_key()` / `create_openai_client(...)`：统一 key 优先级、timeout 和 retry policy。
- `managed_uploaded_pdfs(...)`：上传 PDF，并在正常、请求失败和部分上传失败路径清理文件。
- `usage_value(...)` / `append_jsonl(...)`：统一 usage 字段兼容和 JSONL 追加行为。
- `run_response(client, background, poll_interval, poll_timeout, log, **kwargs)`：创建 response；如果 background 模式开启，就轮询直到 completed 或超时。

### 5.6 `src/refine/loop.py`

整体作用：refine schema 的命令行兼容入口。它本身不放业务逻辑，只 re-export pipeline API，并在直接运行时调用 `main()`。

被谁调用：用户在命令行直接运行。

调用链：

```text
python src/refine/loop.py
  -> src/refine/pipeline/cli.main()
```

导出的函数：

- `build_parser`
- `generate_schema`
- `run_consensus_stage`
- `evaluate_schema`
- `next_round_index`
- `run_round`
- `resume_review`
- `main`

### 5.7 `src/refine/pipeline/cli.py`

整体作用：refine loop 的参数解析和模式分派。

被谁调用：

- `src/refine/loop.py`

调用链：

```text
main()
  -> build_parser()
  -> 校验 --review-ui / --autonomous / --consensus-runs
  -> 如果 --resume-review: resume_review()
  -> 否则: _run_from_start()
```

函数说明：

- `build_parser()`：定义 refine loop 的 CLI 参数。
- `main()`：CLI 总入口。
- `_run_from_start(args)`：从新 round 开始执行；根据 `--autonomous` 决定一轮还是多轮。
- `_load_feedback(args, start_index)`：读取 `--resume-feedback` 指定的 feedback。
- `_print_human_loop_stop(args, round_index)`：默认人工闭环结束时打印下一步提示。

### 5.8 `src/refine/pipeline/rounds.py`

整体作用：管理 `round_N` 状态机，包括一轮 refine 怎么走、review 后怎么 resume。

被谁调用：

- `src/refine/pipeline/cli.py`
- tests 中 mock API steps 做离线验证

调用链：

```text
run_round()
  -> generate_schema()
  -> 如果 consensus_runs > 1: _run_consensus_or_pause()
  -> evaluate_schema()
```

函数说明：

- `next_round_index(out_dir)`：找到下一个可用的 `round_N`。
- `run_round(args, round_index, feedback_in)`：执行一轮 generate / consensus / evaluate。
- `_run_consensus_or_pause(args, draft_path, round_dir, schema_path)`：跑 consensus；如果开启 review UI，则暂停；否则写入自动合并 schema。
- `_print_review_stop(round_dir, queue_path)`：打印 review UI、apply、resume-review 的下一步命令。
- `resume_review(args)`：读取 `consensus/reviewed_schema.yaml`，写成 `round_N/schema.yaml`，然后做 holdout evaluation。

### 5.9 `src/refine/pipeline/steps.py`

整体作用：refine loop 中真正会调用 OpenAI 或 extraction 的步骤集合。

被谁调用：

- `src/refine/pipeline/rounds.py`

函数说明：

- `generate_schema(args, feedback, out_path)`：采样 PDF，调用 `SchemaDiscovery.discover()`，生成 schema draft。
- `run_consensus_stage(args, draft_schema_path, round_dir)`：创建 `SchemaConsensusRefinement`，对 draft 跑 consensus。
- `evaluate_schema(args, schema_text, round_dir)`：采样 holdout PDF，调用 `SchemaExtractor.extract_many()`，再用 `extract/analyze.py` 生成 feedback。

### 5.10 `src/refine/consensus.py`

整体作用：field-level consensus refinement。它把 N 次 candidate patch generation 合并成频率统计、自动合并 schema、稳定性报告和 review queue。

被谁调用：

- `src/refine/pipeline/steps.py`
- 用户也可以单独运行 `src/refine/consensus.py`

调用链：

```text
SchemaConsensusRefinement.refine()
  -> load_alias_config()
  -> 循环 N 次:
       select_samples()
       SchemaDiscovery.discover_patches()
       dump_yaml()
       load_patch_file()
       normalize_patches()
  -> aggregate_patches()
  -> render_frequency_yaml()
  -> render_consensus_schema()
  -> render_report()
  -> compute_patch_stability()
  -> write_patch_stability()
  -> build_review_queue()
  -> write_review_queue()
```

类和函数说明：

- `ConsensusOutputs`：记录 consensus 产物路径和 decisions。
- `SchemaConsensusRefinement.refine(...)`：执行完整 consensus sweep。
- `_run_seed(seed, run_number)`：让每个 consensus run 使用可复现但不同的采样 seed。
- `build_parser()`：定义 standalone consensus CLI 参数。
- `main()`：standalone consensus CLI 入口。

### 5.11 `src/refine/candidates/patch.py`

整体作用：candidate patch 的数据模型和 YAML IO。

被谁调用：

- `src/refine/consensus.py`
- `src/refine/candidates/normalizer.py`
- `src/refine/candidates/aggregator.py`
- `src/refine/artifacts/renderer.py`
- `src/refine/human_review/`

类和函数说明：

- `EvidenceDocument`：记录某个 patch 的证据文档和摘要。
- `EvidenceDocument.from_value(value)`：从 string 或 dict 转成 EvidenceDocument。
- `EvidenceDocument.to_dict()`：输出 YAML-friendly dict。
- `SchemaPatch`：candidate patch 数据结构。
- `SchemaPatch.from_dict(value, source_run)`：从模型 YAML dict 解析 patch。
- `SchemaPatch.validate()`：检查 patch_type 和 field name 是否有效。
- `SchemaPatch.to_dict()`：输出 YAML-friendly dict。
- `load_patch_file(path)`：读取一个 patch YAML 文件并附带 source_run。
- `parse_patch_payload(payload, source_run)`：从 list 或 `{patches: [...]}` 解析 patches。
- `load_yaml(path)`：读取 YAML。
- `parse_yaml_text(text)`：解析 YAML 字符串。
- `dump_yaml(payload, path)`：写 YAML 文件。
- `_require_yaml()`：延迟导入 PyYAML，并在缺失时给出清晰错误。
- `_float_or_zero(value)`：把 confidence 转成 float，失败则为 0。

### 5.12 `src/refine/candidates/normalizer.py`

整体作用：把模型输出的字段名和 group 名变成 canonical snake_case，并应用 alias 配置。

被谁调用：

- `src/refine/consensus.py`

函数说明：

- `load_alias_config(path=None)`：读取 `configs/private_health/aliases.yaml`；未提供 path 时使用默认配置。
- `_string_map(value, source)`：把 YAML mapping 验证并转成 `dict[str, str]`。
- `normalize_patch(patch, field_aliases, group_aliases)`：normalize 单个 patch。
- `normalize_patches(patches, field_aliases, group_aliases)`：normalize patch 列表。
- `canonical_field_name(value, aliases)`：字段名 snake_case 后再应用 alias。
- `canonical_group_name(value, aliases)`：group 名 snake_case 后再应用 alias。
- `_snake_case(value)`：基础 snake_case 转换。

### 5.13 `src/refine/candidates/aggregator.py`

整体作用：跨多轮 patches 做 frequency voting，输出 FieldDecision。

被谁调用：

- `src/refine/consensus.py`
- `src/refine/artifacts/`
- `src/refine/human_review/queue.py`

类和函数说明：

- `FieldDecision`：一个 canonical field 的聚合决策，包含 frequency、decision、aliases、evidence、rationale、reject votes 等。
- `FieldDecision.frequency_label`：返回 `frequency/total_runs`。
- `FieldDecision.reject_votes_label`：返回 `reject_votes/total_runs`。
- `FieldDecision.to_dict()`：输出给 `field_frequency.yaml` 使用的 dict。
- `aggregate_patches(patches, total_runs, core_threshold, conditional_threshold, candidate_threshold)`：按 canonical_name 聚合 patches，并判断 core/conditional/candidate/noise。
- `_build_decision(...)`：从某个字段的 patches 构建 FieldDecision。
- `_most_common(values)`：取出现次数最多的值。
- `_first_non_empty(values)`：取第一个非空值。
- `_sample_rationales(patches, cap)`：保留去重后的 rationale 样本。
- `_unique_evidence(patches)`：合并去重 evidence documents。

### 5.14 `src/refine/candidates/stability.py`

整体作用：基于同一批 candidate patch runs 计算字段稳定性，不额外调用 OpenAI。

被谁调用：

- `src/refine/consensus.py`

函数说明：

- `compute_patch_stability(patches, total_runs)`：计算 stable fields、drifting fields、union、stability 分数。
- `write_patch_stability(payload, path)`：写出 `patch_stability.yaml`。

### 5.15 `src/refine/artifacts/schema_fields.py`

整体作用：schema field payload 的共享 helper，避免 renderer 和 review apply 各自重复 upsert/清洗逻辑。

被谁调用：

- `src/refine/artifacts/renderer.py`
- `src/refine/human_review/queue.py`
- `src/refine/human_review/apply.py`

函数说明：

- `fields_by_name(fields)`：把 schema 的 fields list 转成 name -> field dict。
- `applies_to_from_group(target_group)`：把 consensus group 映射成 schema 的 applies_to。
- `field_payload_from_decision(decision, existing_field=None, include_consensus=False)`：从 FieldDecision 构造或更新 schema field。
- `decision_requires_manual_edit(decision)`：识别混合 action 和 rename/merge/move 等不能安全自动应用的决策。

### 5.16 `src/refine/artifacts/renderer.py`

整体作用：把 consensus decisions 渲染成最终产物。

被谁调用：

- `src/refine/consensus.py`

函数说明：

- `render_consensus_schema(base_schema_path, decisions, output_path)`：把 core/conditional decisions 合并进 base schema，写 `consensus_schema.yaml`。
- `render_frequency_yaml(decisions, output_path)`：写 `field_frequency.yaml`。
- `render_report(decisions, output_path)`：写人类可读的 `consensus_report.md`。

### 5.17 `src/refine/human_review/constants.py`

整体作用：集中保存 human review 相关常量。

被谁调用：

- `src/refine/human_review/queue.py`
- `src/refine/human_review/decisions.py`
- `src/refine/human_review/apply.py`
- `src/refine/human_review/ui.py`
- `src/refine/review.py`

常量说明：

- `MANUAL_EDIT_PATCH_TYPES`：rename/merge/move 这类 schema-level 建议仅供审计，不能作为 field upsert 应用。
- `SUPPORTED_ACTIONS`：支持 `accept`、`reject`、`edit`。
- `QUEUE_FILENAME`：`review_queue.yaml`。
- `DECISIONS_FILENAME`：`review_decisions.yaml`。
- `REVIEWED_SCHEMA_FILENAME`：`reviewed_schema.yaml`。

### 5.18 `src/refine/human_review/queue.py`

整体作用：从 FieldDecision 构造不可变 review queue。

被谁调用：

- `src/refine/consensus.py`
- `src/refine/human_review/ui.py`

函数说明：

- `build_review_queue(decisions, base_schema, total_runs, base_schema_path, generated_at=None)`：生成 review queue dict。
- `_queue_item(decision, existing_field)`：把一个 FieldDecision 转成一条 review item。
- `write_review_queue(queue, path)`：写 `review_queue.yaml`。
- `load_review_queue(path)`：读取并验证 review queue。

### 5.19 `src/refine/human_review/decisions.py`

整体作用：读写 reviewer decisions，并从 decisions 派生 review status。

被谁调用：

- `src/refine/human_review/ui.py`
- `src/refine/human_review/apply.py`

函数说明：

- `empty_decisions(reviewer="")`：生成空 decisions payload。
- `load_review_decisions(path)`：读取 `review_decisions.yaml`。
- `write_review_decisions(decisions_payload, path)`：更新时间戳并写 decisions。
- `upsert_decision(decisions_payload, item_id, action, reviewer_notes="", edited_update=None)`：新增或替换某个 item 的决策。
- `clear_decision(decisions_payload, item_id)`：移除某个 item 的决策，使其回到 pending。
- `decisions_by_id(decisions_payload)`：转成 id -> decision dict。
- `derive_status(queue, decisions_payload)`：得到 pending/accepted/rejected/edited 状态。

### 5.20 `src/refine/human_review/apply.py`

整体作用：把 human review decisions 应用到 base schema，生成 reviewed schema。

被谁调用：

- `src/refine/review.py`
- `src/refine/human_review/ui.py`

函数说明：

- `apply_review(queue, decisions_payload, base_schema)`：核心 apply 逻辑；只应用 accept/edit，reject/pending 不应用。
- `_payload_from_review_item(item, entry, summary)`：根据单条 review item 和 decision 返回要 upsert 的 field payload。
- `apply_review_files(consensus_dir, base_schema_path=None, output_path=None)`：从文件读取 queue/decisions/base schema，写 `reviewed_schema.yaml`。

### 5.21 `src/refine/human_review/ui.py`

整体作用：Streamlit review UI 的实际页面逻辑。

被谁调用：

- `src/review_app.py`
- `streamlit run src/review_app.py`

函数说明：

- `parse_cli_args()`：读取 `--consensus-dir`。
- `load_decisions_or_empty(path)`：有 decisions 文件就读，没有就创建空 payload。
- `save_decision(decisions_path, decisions, item_id, action, notes, edited_update=None)`：保存 accept/reject/edit。
- `main()`：渲染页面、sidebar filters、queue item detail、apply button。
- `_render_review_item(item, status_label)`：渲染左侧 proposal detail。
- `_render_decision_panel(item, decisions, decisions_path)`：渲染右侧 accept/reject/clear/edit 操作区。

### 5.22 `src/refine/review.py`

整体作用：离线 CLI，用于把 review decisions 应用成 reviewed schema。

被谁调用：用户在命令行直接运行。

调用链：

```text
main()
  -> build_parser()
  -> run_apply()
  -> apply_review_files()
```

函数说明：

- `build_parser()`：定义 `review.py apply` 参数。
- `run_apply(args)`：调用 `apply_review_files()` 并打印 applied/edited/rejected/pending summary。
- `main()`：CLI 入口。

### 5.23 `src/review_app.py`

整体作用：Streamlit 的薄入口文件。

被谁调用：用户运行 `streamlit run src/review_app.py`。

调用链：

```text
src/review_app.py
  -> src/refine/human_review/ui.main()
```

### 5.24 `src/extract/extractor.py`

整体作用：用 schema 作为 contract，从 PDF 中抽取 JSON record。

被谁调用：

- `src/refine/pipeline/steps.py`

类和方法说明：

- `SchemaExtractor`：OpenAI-backed extraction owner。
- `extract_one(pdf_path)`：上传单个 PDF，调用 OpenAI，解析 JSON record。
- `extract_many(pdf_paths, out_dir)`：批量抽取；单个 PDF 失败不会中断整个 batch，会写 `_error` record。
- `_parse_json(text)`：清理 markdown fence 并解析 JSON。
- `_log_usage(response, pdf_path, api_key_env, duration)`：写 extraction usage log。

API key、client、PDF 上传/清理、usage 字段读取和 JSONL 追加复用 `src/common/openai_run.py`。

### 5.25 `src/extract/analyze.py`

整体作用：离线分析 extraction JSON，找出 schema 的任务效果问题，并生成下一轮 feedback。

被谁调用：

- `src/refine/pipeline/steps.py`
- 用户也可以单独运行 `src/extract/analyze.py`

类和函数说明：

- `FieldSpec`：schema field 的轻量规格。
- `Analysis`：分析结果数据结构。
- `load_field_specs(schema_text)`：从 schema YAML 读取 field specs。
- `load_records(extraction_dir)`：读取 extraction JSON 文件。
- `is_filled(value)`：判断字段值是否算 filled。
- `analyze(records, specs)`：计算 fill rate、weak fields、required missing、enum violations。
- `print_report(analysis)`：打印分析结果。
- `build_feedback(analysis)`：把分析结果转成下一轮 prompt feedback。
- `build_parser()`：定义 CLI 参数。
- `main()`：离线分析 CLI 入口。

### 5.26 `src/extract/prompts.py`

整体作用：保存 extraction prompt。

被谁调用：

- `src/extract/extractor.py`

内容说明：

- `EXTRACTION_PROMPT`：要求模型按 schema 从 PDF 中抽取 JSON。

### 5.27 `src/stability/signature.py`

整体作用：把 schema YAML 转成可比较的 semantic signature；字段比较包含
type、required、applies_to、values、description 和扩展结构，而不仅是名字。

被谁调用：

- `src/stability/compare.py`
- `src/stability/measure.py`

类和函数说明：

- `SchemaSignature`：保存 product_types、field names、field contracts、hospital_categories、extras_services 等集合。
- `_canonical_names(items, key)`：从 list 中提取规范化名字集合。
- `signature_from_text(text, label)`：从 YAML 文本生成 SchemaSignature。
- `signature_from_file(path)`：从 YAML 文件生成 SchemaSignature。

### 5.28 `src/stability/compare.py`

整体作用：离线比较多个 schema signatures 的 drift。

被谁调用：

- 用户直接运行
- `src/stability/measure.py`

函数说明：

- `jaccard(sets)`：计算 intersection / union 稳定度。
- `occurrence_counts(sets)`：统计每个 item 出现次数。
- `compare(signatures, show_items)`：打印各维度稳定度和整体 verdict。
- `collect_paths(schemas, directory)`：收集待比较 YAML 路径。
- `build_parser()`：定义 CLI 参数。
- `main()`：CLI 入口。

### 5.29 `src/stability/measure.py`

整体作用：会调用 OpenAI API 的稳定性工具；固定同一批 PDF 样本，多次生成 schema，再测 drift。

被谁调用：用户直接运行。

调用链：

```text
main()
  -> select_samples()
  -> for run in N:
       SchemaDiscovery.discover()
  -> signature_from_file()
  -> compare()
```

函数说明：

- `build_parser()`：定义参数。
- `main()`：执行 N 次 discovery 并比较稳定性。

### 5.30 `src/cost/pricing.py`

整体作用：模型价格表和基础成本计算。

被谁调用：

- `src/cost/estimate.py`

类和函数说明：

- `ModelPrice`：模型输入/输出 token 单价。
- `resolve_price(model, input_rate=None, output_rate=None)`：根据模型或手动 override 得到价格。
- `cost_usd(input_tokens, output_tokens, price)`：计算美元成本。

### 5.31 `src/cost/estimate.py`

整体作用：从 token usage logs 估算运行成本。

被谁调用：用户直接运行。

函数说明：

- `load_runs(log_path)`：读取 JSONL usage logs。
- `per_doc_tokens(runs)`：计算平均每文档 input/output tokens。
- `summarise(runs, input_rate, output_rate)`：打印每次 run 和总成本。
- `project(runs, model, verticals, input_rate, output_rate, ext_in, ext_out)`：做跨 vertical 成本投影。
- `parse_vertical(values)`：解析 `--vertical NAME=COUNT`。
- `build_parser()`：定义 CLI 参数。
- `main()`：CLI 入口。

### 5.32 `__init__.py` 文件

这些文件主要用于把目录标记为 Python package，通常不包含业务逻辑：

```text
src/__init__.py
src/common/__init__.py
src/cost/__init__.py
src/extract/__init__.py
src/refine/__init__.py
src/refine/artifacts/__init__.py
src/refine/candidates/__init__.py
src/refine/human_review/__init__.py
src/refine/pipeline/__init__.py
src/schema/__init__.py
src/stability/__init__.py
```

其中 `src/refine/human_review/__init__.py` 和 `src/refine/pipeline/__init__.py` 也承担 re-export 作用，让外部可以从 package 层导入常用函数。

## 6. 如何运行主业务流程

这一节虽然按命令场景拆开写，但业务逻辑仍然是一条线：先生成 schema，再 refine schema。  
`--consensus-runs` 和 `--review-ui` 只是 refine 阶段的开关，不是新的业务流程。

### 6.1 生成初版 schema

```bash
.venv/bin/python src/run.py --seed 42
```

常用参数：

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--samples` | 无 | 显式指定 PDF 文件；设置后不走随机采样 |
| `--input-root` | `data/private_health/raw/PDFs` | PDF 根目录 |
| `--categories` | `combined extras generalhealth hospital` | 采样类别 |
| `--per-category` | `5` | 每个类别选多少家公司/PDF |
| `--seed` | 无 | 采样随机种子 |
| `--model` | `OPENAI_MODEL` 或 `gpt-5` | OpenAI 模型 |
| `--timeout` | `600.0` | OpenAI 请求超时时间 |
| `--keep-uploaded-files` | false | 调试用：不删除上传到 OpenAI 的文件 |
| `--output` | `outputs/private_health/schema.yaml` | schema 输出路径 |
| `--usage-log` | `outputs/private_health/token_usage.jsonl` | token usage JSONL 路径 |

### 6.2 refine schema：不启用字段共识

```bash
.venv/bin/python src/refine/loop.py --seed 42 --eval-seed 7
```

这条命令会：

```text
generate schema
  -> extraction evaluation
  -> feedback.txt
```

### 6.3 refine schema：启用字段共识

```bash
.venv/bin/python src/refine/loop.py \
  --seed 42 \
  --eval-seed 7 \
  --consensus-runs 5
```

这条命令会：

```text
generate schema draft
  -> N-run patch consensus
  -> auto-merge core/conditional fields
  -> extraction evaluation
  -> feedback.txt
```

### 6.4 refine schema：启用字段共识和人工审核

第一步：生成 review queue 并暂停。

```bash
.venv/bin/python src/refine/loop.py \
  --seed 42 \
  --eval-seed 7 \
  --consensus-runs 5 \
  --review-ui
```

第二步：打开 review UI。

```bash
streamlit run src/review_app.py -- \
  --consensus-dir outputs/private_health/refine/round_1/consensus
```

第三步：应用审核结果。

```bash
.venv/bin/python src/refine/review.py apply \
  --consensus-dir outputs/private_health/refine/round_1/consensus
```

第四步：用 reviewed schema 做 holdout evaluation。

```bash
.venv/bin/python src/refine/loop.py \
  --resume-review outputs/private_health/refine/round_1
```

### 6.5 refine loop 常用参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--input-root` | `data/private_health/raw/PDFs` | PDF 根目录 |
| `--per-category` | `5` | schema discovery 每类采样数 |
| `--seed` | `42` | discovery 采样 seed |
| `--eval-per-category` | `2` | holdout evaluation 每类采样数 |
| `--eval-seed` | `7` | evaluation 采样 seed，建议和 `--seed` 不同 |
| `--model` | `gpt-5` | OpenAI 模型 |
| `--timeout` | `600.0` | OpenAI 超时 |
| `--out-dir` | `outputs/private_health/refine` | refinement round 输出根目录 |
| `--rounds` | `1` | autonomous 模式最多轮数 |
| `--consensus-runs` | `1` | 大于 1 时开启 consensus |
| `--review-ui` | false | consensus 后停下来生成 review queue |
| `--resume-review` | 无 | 对人工审核后的 round 做 holdout evaluation |
| `--autonomous` | false | 自动把 feedback 带入下一轮 |
| `--resume-feedback` | 无 | 从某个 feedback.txt 开始下一轮 |

约束：

- `--review-ui` 要求 `--consensus-runs > 1`。
- `--review-ui` 不能和 `--autonomous` 同时使用。
- 默认 `--consensus-runs 1` 表示不启用 consensus。

## 7. 辅助工具如何运行

这些命令不是主业务线，但可以单独调试某个步骤。

### 7.1 单独跑字段共识

```bash
.venv/bin/python src/refine/consensus.py \
  --base-schema outputs/private_health/schema.yaml \
  --runs 5
```

### 7.2 单独应用 review decisions

```bash
.venv/bin/python src/refine/review.py apply \
  --consensus-dir outputs/private_health/refine/round_1/consensus
```

可选参数：

```text
--base-schema
--out
```

### 7.3 单独分析 extraction failures

```bash
.venv/bin/python src/extract/analyze.py \
  --schema outputs/private_health/refine/round_1/schema.yaml \
  --extractions outputs/private_health/refine/round_1/extractions \
  --feedback-out outputs/private_health/refine/round_1/feedback.txt
```

### 7.4 比较已有 schema 的 drift

```bash
.venv/bin/python src/stability/compare.py \
  --schemas run_1.yaml run_2.yaml run_3.yaml \
  --show-items
```

### 7.5 调用 OpenAI API 测量 schema 稳定性

```bash
.venv/bin/python src/stability/measure.py \
  --runs 3 \
  --seed 42 \
  --show-items
```

### 7.6 估算成本

```bash
.venv/bin/python src/cost/estimate.py \
  --log outputs/private_health/token_usage.jsonl
```

项目级估算：

```bash
.venv/bin/python src/cost/estimate.py \
  --project \
  --vertical private_health=1105 \
  --vertical energy=800 \
  --vertical mobile_plans=500
```

## 8. 关键产物

### 8.1 schema 生成产物

```text
schema.yaml
token_usage.jsonl
```

### 8.2 字段共识产物

```text
candidate_patches/run_001.yaml
field_frequency.yaml
consensus_report.md
consensus_schema.yaml
patch_stability.yaml
review_queue.yaml
```

说明：

- `candidate_patches/`：每轮模型提出的候选 patch。
- `field_frequency.yaml`：字段出现频率、证据、拒绝票。
- `consensus_report.md`：人类可读报告。
- `consensus_schema.yaml`：按阈值自动合并后的 schema。
- `patch_stability.yaml`：字段 drift 报告。
- `review_queue.yaml`：给人工审核 UI 使用的队列。

### 8.3 人工审核产物

```text
review_decisions.yaml
reviewed_schema.yaml
```

说明：

- `review_decisions.yaml`：UI 每次 accept/reject/edit 都会写。
- `reviewed_schema.yaml`：只应用 accepted/edited updates。

### 8.4 refine 轮次产物

```text
round_N/schema_draft.yaml
round_N/schema.yaml
round_N/consensus/
round_N/extractions/
round_N/feedback.txt
```

说明：

- `schema_draft.yaml`：consensus 开启时保留的单次生成 draft。
- `schema.yaml`：本轮实际被 evaluation 的 schema。
- `feedback.txt`：下一轮 generation 的反馈。

## 9. 字段共识（consensus）决策逻辑

candidate patch 支持：

```text
add_field
rename_field
merge_fields
move_field_group
update_description
add_alias
reject_field
```

字段频率决策：

| 频率比例 | decision |
| --- | --- |
| `>= 0.8` | `core` |
| `>= 0.5` | `conditional` |
| `>= 0.2` | `candidate` |
| `< 0.2` | `noise` |

`consensus_schema.yaml` 只考虑以下 frequency 层级：

```text
core
conditional
```

Frequency 不代表 requiredness。只有单一、完整、无 reject vote 的安全 action
可以无人值守合并；混合 action 必须保存显式 field edit。`rename_field`、
`merge_fields`、`move_field_group` 是 audit-only，不能作为 field upsert 应用，
必须直接修改 base schema。

`reject_field` 不改变 frequency 分类，但任何 reject vote 都会阻止无人值守
合并。只有 reject、没有正向 patch 的字段不会生成 FieldDecision。

## 10. alias 归一化

字段和 group 的 canonicalization 由：

```text
configs/private_health/aliases.yaml
```

控制。

示例：

```yaml
field_aliases:
  yearly_limit: annual_limit
  company_name: fund_name

group_aliases:
  extras: extras_cover
  hospital: hospital_cover
```

如果要调整 canonical name，优先改配置文件，不要改 `normalizer.py`。
归一化时 observed field name 只转为 snake_case 并作为 alias 保留；投票使用映射后的 canonical name。

## 11. 哪些命令会调用 OpenAI

会调用 OpenAI、需要 API key、可能产生费用：

```text
src/run.py
src/refine/loop.py
src/refine/consensus.py
src/stability/measure.py
refine loop 内部的 SchemaExtractor.extract_many()
```

离线命令：

```text
src/refine/review.py apply
src/extract/analyze.py
src/stability/compare.py
src/cost/estimate.py
python -m unittest discover -s tests
```

## 12. 测试和验证

运行离线测试：

```bash
.venv/bin/python -m unittest discover -s tests
```

编译检查：

```bash
.venv/bin/python -m compileall src tests
```

如果修改 CLI 参数，再跑对应 help：

```bash
.venv/bin/python src/run.py --help
.venv/bin/python src/refine/loop.py --help
.venv/bin/python src/refine/consensus.py --help
.venv/bin/python src/refine/review.py --help
```

测试不会调用 OpenAI，也不需要真实 PDFs。
