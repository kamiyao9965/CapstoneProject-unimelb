# Health / Travel 实用手册

面向项目组的开发、实验和演示。依据 `main` 的 `62aaa7a` 版本核对，更新于 2026-09-10。所有命令从仓库根目录执行；示例 PDF 路径需要替换为自己的文件。Python 接口见 [api.md](../api.md)，实现边界见 [architecture.md](architecture.md)。

## 1. 先选对入口

| 我想做什么 | 使用哪个入口 | 是否调用外部服务 |
| --- | --- | --- |
| 用页面选择领域、配置并运行 | `src/tool_app.py` | 取决于执行的操作 |
| 从 PDF 发现字段 | `src/run.py discover` | 模型 API |
| 用已有 schema 提取 PDF | `src/run.py extract` / `batch` | 模型 API |
| 生成字段建议、人工审核、继续流程 | `src/refine/loop.py` + `src/review_app.py` | 生成及提取阶段调用模型 |
| 查看已有提取结果的问题 | `src/schema_application/analyze.py` | 否 |
| 比较已有 schema 的稳定性 | `src/stability/compare.py` | 否 |
| 收集 Travel 公开文档 | `src/run.py crawl` | 访问保险公司网站 |
| 审批 Travel 入库契约 | `src/canonical_review_app.py` | 否 |
| 生成 SQL 预览 / 真正建表、入库 | `canonical-compile` / `storage-init`、`storage-load` | 仅后两者连接 PostgreSQL |

当前是一套 Python 引擎、三个本地 Streamlit 页面和文件产物，没有 REST API 服务。

### 两个 vertical 的实际差异

| 配置 | Health | Travel |
| --- | --- | --- |
| vertical code | `private_health` | `travel_insurance` |
| manifest | `configs/private_health/manifest.json` | `configs/travel_insurance/manifest.json` |
| 采样目录类别 | `combined`、`extras`、`generalhealth`、`hospital` | `pds` |
| 提取单位 | 一份文档一个对象 | 一份文档内多个产品 / product release |
| 产品类型 | `hospital`、`extras`、`generalhealth`、`combined` | `international_single_trip`、`international_multi_trip`、`domestic`、`inbound`、`business`、`cruise` |
| taxonomy | `hospital_categories`、`extras_services` | `coverage_categories` |
| 默认 proposal 次数 | 1 | 5 |
| loop 自动 holdout / feedback | 支持 | 未配置 |
| labelled batch evaluation | 支持，需本地标签数据 | 不支持 |
| 文档采集 / Canonical / PostgreSQL | 未启用 | 支持 |

Travel 的 `pds` 表示文档类型；它不等于 `domestic` 等产品类型。流程不会把 `pds` 当作产品分类真值。

## 2. 安装与环境配置

已有项目虚拟环境时直接使用 `.venv/bin/python`。首次安装：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python src/run.py --help
```

项目依赖支持范围见 [README](../README.md#requirements) 和 [依赖政策](dependency-policy.md)。仓库不包含 API key、原始 PDF 和标签数据。

主流程从进程环境读取配置，**不会自动加载 `.env` 文件**。可在启动 CLI / Streamlit 的终端中设置以下非敏感选项，并用自己的安全方式注入 API key：

```bash
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-5
export LLM_DOCUMENT_INPUT=markdown
```

如果已将自己的配置保存在本地 `.env`，在启动前手动导入：

```bash
set -a
source .env
set +a
```

这会执行该文件中的 shell 内容，因此只用于自己维护、可信的 `.env`；不要把配置文件或凭证提交到仓库。

| 环境变量 | 含义 |
| --- | --- |
| `MY_OPENAI_API_KEY` / `OPENAI_API_KEY` | OpenAI 凭证，前者优先 |
| `ANTHROPIC_API_KEY` | Anthropic 凭证 |
| `DEEPSEEK_API_KEY` | DeepSeek 凭证 |
| `LLM_PROVIDER`、`LLM_MODEL`、`LLM_DOCUMENT_INPUT` | 模型选项；CLI 显式参数优先 |
| `OPENAI_MODEL` | OpenAI 场景下未设置 `LLM_MODEL` 时的后备模型配置 |
| `KONKRD_DATA_ROOT` | Health 数据集根目录，其下应有 `data/private_health/...` |
| `KONKRD_DATABASE_URL` | Travel 入库使用的 PostgreSQL 连接串 |

未指定时，主流程选择 `openai / gpt-5 / markdown`。其他 provider 需要指定模型。仓库允许的模型模式见 [model_capabilities.json](../configs/model_capabilities.json)；该表表示本地校验规则，不保证你的账户具有模型访问权限。

主流程统一先用 PDFingestor 将 PDF 转成有页码、文本块和表格结构的文本，再发送模型请求。保留 `markdown` 模式；底层 provider 支持原生 PDF 并不代表主流程可以切换为 `pdf`。扫描件能否解析需要单独检查，默认没有自动视觉 OCR 流程。

### 数据摆放

建议保持 `公司 / 类别 / 文件.pdf` 结构：

```text
konkrd-data/data/private_health/raw/PDFs/
  insurer_a/hospital/example.pdf
  insurer_a/extras/example.pdf
  insurer_b/hospital/example.pdf

data/travel_insurance/raw/PDFs/
  allianz/pds/example.pdf
  covermore/pds/example.pdf
```

这是结构示意，不代表仓库附带这些文件。Health 的其余类别也需要准备。自动采样要求每个类别有足够的不同公司，并按 PDF 内容去重：`--per-category 5` 不是从同一家公司的五个文件取样。复制同一 PDF 不会增加样本数。holdout 还会排除 discovery 和 proposal 已用过的内容。

Travel 默认路径不受 `KONKRD_DATA_ROOT` 影响。单次 discovery / batch / loop 可用 `--input-root` 指定路径；如果后续需要 storage，须让 manifest 的 `input_root` 与实际存储来源一致。

## 3. 五分钟跑通一次提取

已有可用 schema 时，不必先重复 discovery。以下命令会调用模型 API。

Health，使用自己生成或审核过的 schema：

```bash
.venv/bin/python src/run.py extract \
  --manifest configs/private_health/manifest.json \
  --schema outputs/private_health/schema.json \
  --pdf path/to/health-policy.pdf
```

将 `path/to/health-policy.pdf` 换成实际路径；`outputs/private_health/schema.json` 也必须已存在。

Travel，可直接使用仓库已批准的 Canonical 契约：

```bash
.venv/bin/python src/run.py extract \
  --manifest configs/travel_insurance/manifest.json \
  --schema configs/travel_insurance/canonical_schema_v1.json \
  --pdf data/travel_insurance/raw/PDFs/allianz/pds/example.pdf
```

成功后查看终端打印的 JSON 路径。`data` 才是模型提取内容；Travel 的数据在 `data.products` 数组中。字段缺失可能表现为 `null`，需结合 `_unfilled`、`_notes` 查看原因。结构校验通过不等于保险条款已被准确理解，演示前应对照原 PDF 检查几项关键值。

默认结果位于对应 vertical 的 `outputs/.../extractions/`，保留输入根目录内的相对路径。再次运行会另存带数字后缀的文件。指定 `--output` 时，目标已存在会拒绝执行，改用新文件名即可。

### 用统一 UI 完成同样操作

```bash
.venv/bin/python -m streamlit run src/tool_app.py --server.address 127.0.0.1
```

1. 在页面先选择 Health 或 Travel。
2. 选择操作，填写 PDF、schema、模型和输出参数。
3. 核对命令预览和当前 vertical；对需要确认的操作勾选确认，再执行。
4. 查看本次命令、退出状态和打印的产物位置。

切换 vertical 或操作后表单、确认和结果状态会重置；修改执行参数需要重新确认。页面同步运行 CLI，没有后台任务队列。凭证来自启动页面的进程环境，修改终端环境后应重启页面进程。

## 4. Health：发现 → 审核 → holdout → feedback

### A. 只发现一个初稿

```bash
.venv/bin/python src/run.py discover \
  --manifest configs/private_health/manifest.json \
  --per-category 2 --seed 42 \
  --output outputs/private_health/experiments/demo/schema.json
```

四个类别各取两家公司，共八份内容不同的 PDF。样本不足时降低数量或补齐数据。也可用 `--samples path/to/a.pdf path/to/b.pdf` 显式指定样本，此时不走自动采样。

产物是 `discovered_schema` envelope，schema 在 `data` 中。CLI 会打印实际保存路径；discovery 遇到文件重名会使用可用后缀路径，即使传了 `--output` 也不要假设覆盖原文件。

### B. 需要逐项审核的推荐流程

```bash
.venv/bin/python src/refine/loop.py \
  --manifest configs/private_health/manifest.json \
  --out-dir outputs/private_health/experiments/review-demo \
  --per-category 2 --eval-per-category 1 \
  --seed 42 --eval-seed 7 --review-ui
```

首次运行在 `round_1/consensus/` 生成 review queue 后停止；重用实验目录时会创建下一个空闲 `round_N`，以下路径需相应调整。Health 默认一次 proposal；加上 `--review-ui` 即使只有一次也会生成审核队列。

打开审核页面（如果主 UI 已占用 8501，这里使用 8502）：

```bash
.venv/bin/python -m streamlit run src/review_app.py \
  --server.address 127.0.0.1 --server.port 8502 -- \
  --consensus-dir outputs/private_health/experiments/review-demo/round_1/consensus
```

逐项选择 Accept / Reject / Edit，保存决策，然后在页面 Apply；也可以保存后退出页面，用 CLI Apply，二选一：

```bash
.venv/bin/python src/refine/review.py apply \
  --consensus-dir outputs/private_health/experiments/review-demo/round_1/consensus
```

Apply 默认生成该目录下的 `reviewed_schema.json`。Pending 和 Reject 不会应用；Apply 不代表所有条目都完成审核。queue、decisions 和基础 schema 必须属于同一次审核，不能在不同实验之间搬运或拼接。

随后继续本轮，保留原来的 manifest、输入根目录和实验输出目录：

```bash
.venv/bin/python src/refine/loop.py \
  --manifest configs/private_health/manifest.json \
  --out-dir outputs/private_health/experiments/review-demo \
  --eval-per-category 1 --eval-seed 7 \
  --resume-review outputs/private_health/experiments/review-demo/round_1
```

恢复会校验审核结果，提取未用于构建 schema 的 holdout PDF，生成 `refinement_feedback.json`，再发布实验目录下的 `final_schema.json` 或带后缀版本。它不会替换生产 schema。

**Apply 的 `--out` 可另存审核结果，但 `--resume-review` 固定读取 `consensus/reviewed_schema.json`。** 已完成 Apply 后再修改 decisions，会使旧审核产物失效；需要重新生成与当前决策一致的审核产物。为保留审计链，日常迭代优先创建新实验，而不是覆盖旧结果。

### C. 根据 feedback 开始下一轮

```bash
.venv/bin/python src/refine/loop.py \
  --manifest configs/private_health/manifest.json \
  --out-dir outputs/private_health/experiments/feedback-demo \
  --per-category 2 --eval-per-category 1 \
  --resume-feedback outputs/private_health/experiments/review-demo/round_1/refinement_feedback.json
```

feedback 必须是当前格式的成功产物，且 provenance 中的 vertical 匹配。默认每次运行一轮；只有 `--autonomous --rounds N` 才自动迭代 N 轮，并增加模型调用。`--autonomous` 不能与 `--review-ui` 同用。

### D. 批量提取与标签评估

```bash
.venv/bin/python src/run.py batch \
  --manifest configs/private_health/manifest.json \
  --schema outputs/private_health/experiments/review-demo/final_schema.json \
  --evaluate
```

先确认打印的 final schema 实际文件名。`--evaluate` 使用 Health 的本地 labelled 数据，默认根目录为 `konkrd-data/data/private_health/labelled`；没有标签时先去掉该参数。batch 会对输入目录中的 PDF 调用模型，并把结果写到 manifest 的输出根目录；它没有 `--out-dir` 参数。需隔离批次时配置独立输出根目录或归档已完成的实验数据。

## 5. Travel：采集 → 发现与审核 → 提取 → 可选入库

### A. 采集公开文档

```bash
.venv/bin/python src/run.py crawl \
  --manifest configs/travel_insurance/manifest.json \
  --discovery-only
```

先检查生成的 acquisition metadata，再去掉 `--discovery-only` 下载 PDF。这个选项只是不下载 PDF，仍会访问网站并写 metadata。采集来源由 [sources.json](../configs/travel_insurance/sources.json) 管理；`--insurer CODE` 可重复，用配置中的 code 筛选公司。

采集支持 PDS、SPDS、brochure、TMD、FSG；当前 discovery 自动采样仅使用 `pds` 目录。

### B. 生成建议并审核

```bash
.venv/bin/python src/refine/loop.py \
  --manifest configs/travel_insurance/manifest.json \
  --out-dir outputs/travel_insurance/experiments/review-demo \
  --per-category 2 --seed 42 --review-ui
```

默认生成五次 proposal。按第 4 节同样的方法打开 `round_N/consensus`、保存决策、Apply，再使用 Travel manifest 和本次实验目录执行 `--resume-review`。

Travel 默认仅自动提升符合规则的 core 建议，人工队列保留需要决定的项。`product_name`、`product_type` 受保护。投票频率不是准确率；高频建议仍需检查语义。

Travel 恢复后会发布 final schema，**不会运行 Health 的自动 holdout / feedback 闭环**。可直接用这个 discovered schema 做 `extract` / `batch`；若要入库，则继续下一步 Canonical 审批。

### C. 审批入库契约

```bash
.venv/bin/python -m streamlit run src/canonical_review_app.py \
  --server.address 127.0.0.1 --server.port 8503 -- \
  --manifest configs/travel_insurance/manifest.json \
  --schema outputs/travel_insurance/experiments/review-demo/final_schema.json \
  --output outputs/travel_insurance/experiments/review-demo/canonical_approved.json
```

页面从 discovered schema 构建 candidate，展示字段和 storage 映射；检查后填写 reviewer、rationale 并确认批准。输入内容或输出路径变化会使原确认失效。这里不调用模型、不建表、不入库，也不会覆盖仓库已批准版本。

离线生成提取契约和 SQL 预览：

```bash
.venv/bin/python src/run.py canonical-compile \
  --manifest configs/travel_insurance/manifest.json \
  --schema outputs/travel_insurance/experiments/review-demo/canonical_approved.json \
  --output-dir outputs/travel_insurance/experiments/review-demo/compiled
```

新目录包含 `extraction_contract.json` 和 `vertical_table.sql`。输出目录必须尚不存在。编译不执行 SQL；candidate 不能作为已批准契约使用。

### D. 按批准版本提取并入库

使用 **同一份 approved schema** 重新执行 `extract`，再将成功 extraction 文件交给 storage。不能仅把 discovered 结果的版本号改成 approved 版本。

下面两条会真正修改 `KONKRD_DATABASE_URL` 指向的 PostgreSQL，执行前确认目标环境：

```bash
.venv/bin/python src/run.py storage-init \
  --manifest configs/travel_insurance/manifest.json \
  --schema outputs/travel_insurance/experiments/review-demo/canonical_approved.json

.venv/bin/python src/run.py storage-load \
  --manifest configs/travel_insurance/manifest.json \
  --schema outputs/travel_insurance/experiments/review-demo/canonical_approved.json \
  --artifact outputs/travel_insurance/extractions/allianz/pds/example.json \
  --insurer-code allianz
```

`--artifact` 必须换成实际提取产物。源 PDF 仍需存在，且位于 manifest 输入根目录的 `insurer/document_type/` 下，insurer code 与参数一致。SQLite 不支持。建表仅创建缺少的表，不执行已有表的版本迁移；加载在一个事务中完成，相同身份且内容一致的数据可重复加载，身份冲突会失败。

## 6. 读懂输出、质量和成本

### 产物去哪找

| 文件 / 目录 | 用途 |
| --- | --- |
| `outputs/<vertical>/schema*.json` | 单次 discovery 结果 |
| `<实验目录>/round_N/schema_draft.json` | 有 consensus 时保留的初稿 |
| `<实验目录>/round_N/schema*.json` | 本轮采用的 schema；恢复时可能有后缀 |
| `<实验目录>/round_N/consensus/` | patch、频率、队列、决策和审核结果 |
| `<实验目录>/round_N/extractions/` | Health holdout 的逐文档提取 |
| `<实验目录>/round_N/refinement_feedback.json` | Health 本轮反馈 |
| `<实验目录>/final_schema*.json` | 本次发布的 schema，按日志选择具体版本 |
| `outputs/<vertical>/extractions/` | 主 CLI 单份 / 批量提取结果 |
| `errors/<stage>/` | 对应输出根目录下的失败诊断 |
| `pdfingestor_cache/` | 相应阶段使用的本地解析缓存，可重新生成 |

Discovery、consensus、review、holdout 使用 envelope：`status`、`provenance`、`data`、`error`。主 CLI 的 extract / batch 保留 `ExtractionResult` 格式，顶层有 `vertical`、`schema_version`、`source_path`、`data`，没有 envelope 的 `status`。两种格式都不能只看文件存在就当作有效数据。Python 接入应使用 [api.md](../api.md) 中的对应加载器。

### 分析已完成的提取

```bash
.venv/bin/python src/schema_application/analyze.py \
  --manifest configs/private_health/manifest.json \
  --schema outputs/private_health/experiments/review-demo/final_schema.json \
  --extractions outputs/private_health/experiments/review-demo/round_1/extractions
```

使用与提取相匹配的 discovered schema 和独立结果目录。这个分析入口不接受 Canonical schema。加 `--feedback-out 新文件.json` 可以保存 feedback。

填充率衡量适用记录里有多少字段被填，不等于值准确率。Health 根据目录类别确定适用范围，模型猜测的 `product_type` 不改变分母。Travel 没有可信产品标签，产品分类准确率和特定产品字段填充率显示 N/A，通用字段仍可分析；多个产品会展开为多条记录，记录数不一定等于 PDF 数。

比较同一领域两个已有 schema，不调用模型：

```bash
.venv/bin/python src/stability/compare.py \
  --manifest configs/private_health/manifest.json \
  --schemas outputs/private_health/schema.json outputs/private_health/schema_1.json
```

稳定性衡量字段、产品类型和 taxonomy 的变化，不代表抽取准确率。`src/stability/measure.py` 会在同一组样本上反复调用 discovery，适合单独预算的稳定性实验。

### 查看成本

```bash
.venv/bin/python src/cost/estimate.py \
  --manifest configs/private_health/manifest.json \
  --log outputs/private_health/experiments/review-demo/token_usage.jsonl
```

loop 的 discovery / proposal usage 在实验根目录；holdout usage 在 `round_N/extraction_usage.jsonl`；主 CLI extract / batch usage 在 `outputs/<vertical>/extraction_usage.jsonl`。一次实验可能需要分别查看多个日志。每个逻辑请求最多进行初次生成加两次结构修复；发生修复时会增加 token 消耗。

估算器的内置价格不是账单。需精确估算时，用已核实的每百万 token 单价传入 `--input-rate`、`--output-rate`；混合模型日志应留意各模型费率。日志不要提交到 Git。

## 7. 调整 manifest 和 prompt

每个领域的配置都放在同一个包里：

```text
configs/<vertical>/
  manifest.json
  prompts/discovery.md
  prompts/patch.md
  prompts/extraction.md
```

- `manifest.json`：路径、能力开关、文档分类、产品类型、taxonomy、身份字段、consensus 和审核策略。
- `discovery.md`：模型如何从文档提出公共 schema。
- `patch.md`：模型如何针对已有 schema 提出修改。
- `extraction.md`：按契约提取的领域指引。

修改前先看两个现有 manifest，不另建 Python 中的领域列表或 prompt 列表。修改后用 `load_vertical_manifest()` 离线校验，再选择小样本实验。JSON Schema 和业务校验仍是最终边界，prompt 不能放宽契约。

本次不做 aliases 管理或同义词合并，也不添加第三个 vertical。将来符合现有字段、文档和流程模型的领域可通过配置包接入；如果需要新采集协议、评估数据适配或新的运行步骤，仍可能需要代码。避免为了“完全零代码”提前设计通用规则语言。

## 8. 常见问题与恢复

| 现象 | 排查和处理 |
| --- | --- |
| UI 看不到某个操作 | 检查当前 vertical 的 capability；Health 没有 storage，Travel 没有 labelled evaluation |
| 设置 `.env` 后仍提示缺少 key | 主流程不自动读 `.env`；确认环境已注入启动 CLI / UI 的进程，勿打印 key |
| 模型 / document input 被拒绝 | 核对本地 capability 配置；主流程使用 `markdown`，非 OpenAI 显式指定模型 |
| `Not enough unique PDFs` | 检查类别目录、每类不同公司数、内容去重和 holdout 排除；降低采样量或补数据 |
| PDF 没有可用文本 / 表格错位 | 先用 PDFingestor 离线检查结果；扫描件及复杂表格需处理后再做模型实验 |
| JSON 校验反复失败 | 查看 `errors/<stage>/` 的字段路径和原因，检查 prompt 与 manifest / schema 是否一致；失败数据不能继续分析 |
| `Refusing to overwrite` / `FileExistsError` | 使用新输出文件或新实验目录；不要删除审核链中的文件来强行复用路径 |
| Apply 成功后仍有 Pending | 正常；未决定的建议不会应用。是否结束审核由项目组决定 |
| `--resume-review` 拒绝 | 确认传的是 `round_N`，存在默认 `reviewed_schema.json`，queue / decisions / base 与该产物一致 |
| 旧审核队列无法恢复 | 缺少身份绑定的历史队列只能审计；用当前流程重新生成，或在其原版本环境处理 |
| Travel 报告指标 N/A | 没有产品类型真值，不是自动按 `pds` 推断；不要把 N/A 填成 0 |
| 入库拒绝来源 / 版本 | 检查 approved schema、原 PDF、manifest 输入根目录、公司目录和 artifact；不要手改身份字段绕过校验 |
| 昨天任务中断，今天如何继续 | 有有效审核产物时 `--resume-review`；有 Health feedback 时 `--resume-feedback`；其他中途失败建议开新实验，不具备任意 API 调用的断点续跑 |

## 9. 日常验收

代码变更后的项目离线检查：

```bash
.venv/bin/python -m compileall src tests
.venv/bin/python -m unittest discover -s tests
.venv/bin/python src/run.py --help
```

团队演示前，另外选少量实际 PDF，记录 manifest、schema 版本、模型、种子、样本和输出路径，并人工对照提取值。离线测试不验证真实 API、PDF 解析质量或数据库连接。

本文的命令参数和 Python 示例按当前 main 核对；编写文档时未执行付费模型调用、公开网站采集或 PostgreSQL 写入。
