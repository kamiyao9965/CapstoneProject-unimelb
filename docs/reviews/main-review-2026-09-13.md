# Main 审查：业务有效性、正确性与精简

审查日期：2026-09-13。基线：`main`，`2e4465f`。审查开始时工作区干净。本次仅审查与离线复现，没有修改业务代码、删除兼容入口或更改依赖。

## 结论

Health / Travel 已共用 schema 发现、patch 共识、人工审核和提取引擎；manifest / prompt 表达领域差异的方向成立。代码大体对应项目流程，没有必要推倒重建。

但当前版本仍有会影响评估可信度、恢复正确性和入库身份的缺陷。可以继续用作项目组的开发、实验和演示工具；不能仅凭离线测试通过，就声称已经证明它能稳定生成准确、可比较、可入库的保险数据。

5,000 行不是验收门槛。狭义核心包约 4,352 行有效 Python，加主 CLI 为 4,970；整个非爬虫运行代码是 9,894 行有效 Python。前一个小计依赖后面的支撑模块，不能当作完整系统体量。

## 需要修复的发现

### R1 · P1：Envelope 入库未检查提取时的 vertical / schema version

位置：[storage/service.py](../../src/storage/service.py)，`_artifact_values()` 第 214–233 行；对照同函数第 239–244 行。

同一个存储入口支持 envelope 与旧 `ExtractionResult`。旧格式检查 vertical / schema_version；envelope 分支验证成功状态和来源后直接返回 data，没有检查已传入的 expected_vertical / expected_schema_version。后续按所选 approved schema 创建 load plan。

离线使用现有存储测试 fixture，经真实 `prepare_storage_load()` 复现：

- envelope provenance 声明 Travel、`different-schema-version`，仍通过，load plan 版本为所选 `1.0.0`。
- envelope provenance 声明 Health，但使用结构兼容的提取 data 和合法 Travel 来源，也通过并被准备为 Travel 数据。

影响：旧版或错误领域产物可能被记录在另一个批准契约下，失去“这份结果按哪版 schema 提取”的可信关联。这里证明的是完整入库预检接受了错误身份；未连接数据库实际写入。

建议：在两种格式解析完成后，共用一次 vertical、schema version、source identity 校验。显式冲突必须拒绝；历史缺少身份的 envelope 使用明确的兼容策略，不能默认为所选批准版本。补两种格式的对称测试。

### R2 · P2：重试审核后的 holdout 会混入旧成功结果，并可能在额外调用后失败

位置：[refine/pipeline/steps.py](../../src/refine/pipeline/steps.py)，`evaluate_schema()` 第 137–153、170–174 行；[schema_application/extractor.py](../../src/schema_application/extractor.py) 第 235–245 行。

同一轮始终写 `round_N/extractions/`。原子 writer 保留旧成功文件，重试会产生带后缀的新文件；分析随后扫描整个目录，没有只采用本次 `extract_many()` 返回的路径，也没有按 source / schema 去重。feedback 则固定写 `refinement_feedback.json`。

离线运行真实 `evaluate_schema()` 和 `extract_many()`，只替换采样与单次模型提取边界：

1. 两份 PDF，第一份成功、第二份模拟中断。
2. 重试两份均成功，分析得到 `documents=3`、`error_docs=1`，实际仅两个不同来源。
3. 已完成后再次调用，又执行两次提取，再因 feedback 已存在抛 `FileExistsError`。

同一复现中 feedback 文本仍说没有系统性失败。`build_feedback_instructions()` 在有成功记录时没有把 error_docs 加入提示，应与恢复修复一起处理。

影响：重复记录改变填充率等指标的权重；旧失败残留被混入新尝试；已经完成的恢复会再次消耗模型调用后失败。

建议：明确“一次评估尝试”的输出集合。最小实现可为每次尝试使用新目录，分析只读取这次返回的文件；或者只复用来源和 schema 身份均匹配的成功结果。调用模型前识别已完成 feedback。无需任务调度器或通用 checkpoint 框架。

### R3 · P2：Health 标签评估静默漏计无标识条目与重复条目

位置：[evaluation/metrics.py](../../src/evaluation/metrics.py)，`_flatten()` 第 394–401 行和 `_keyed_items()` 第 546–549 行。

列表对象仅在具有 category / service / name 时进入统计；缺少这些键的对象被跳过。同名条目落入字典中的同一个键，后面的值覆盖前面的值。公共提取契约允许 `list[object]`，这两种结果可以通过当前结构和业务校验。

离线复现使用经过真实公共 extraction contract 校验的 Health 平面记录，包含 services 字段：

- 一个正确 GeneralDental 条目，加一个 `benefit_name=Invented` 的多余条目。
- 同一个 GeneralDental 重复两次，covered 先为 false、后为 true。

两者对单条正确标签都得到 `field_precision=1.0`、`hallucination_rate=0.0`、`service_precision=1.0`。

影响：模型生成了额外内容或自相矛盾的数据，质量报告仍可能显示满分。这个问题直接影响项目对抽取质量的判断。

建议：无标识条目和重复身份应显式计入 invalid / unmatched / duplicate 指标，或使该记录评估失败。保持已有 Health 标识规则即可，不需要 aliases 管理或任意嵌套 schema 框架。

### R4 · P2：PDF 没有任何可用内容时，仍可进入模型调用并被保存为成功

位置：[PDFingestor/adapter.py](../../src/PDFingestor/adapter.py) 第 38–52、62–69 行；[schema_application/extractor.py](../../src/schema_application/extractor.py) 第 148–155 行。Discovery 使用相同渲染入口。

PDFingestor 可以合法返回零 block 页面；渲染器仍生成文件名、路径、hash 和页码，因此得到非空字符串。下游没有在模型调用前检查是否有实际文本、表格或视觉提取内容。

用程序在临时目录生成一页真实空白 PDF，经真实 PDFingestor 得到 `blocks=[]`，渲染后仍有 metadata 文本。使用 fake provider 返回结构合法的记录，真实 extractor 调用 provider 一次并写出 `status=success`、`product_name=Example` 的文件。

影响：扫描件没有可用文本层或文档内容未被解析出来时，系统可能继续付费调用，并只凭返回结构合法接受缺乏文档依据的数据。复现证明边界缺失，不代表已经观察到某个真实模型在该输入上编造数据。

建议：在公共 PDF-to-prompt 入口逐文档检查实际可用内容；零内容先报明确错误。支持 OCR 可以另行安排，不应为了修复这个边界就增加一套 OCR 平台。

## 业务逻辑与代码的匹配程度

| 目标 | 当前判断 |
| --- | --- |
| Health / Travel 共用核心引擎 | 基本成立；配置差异主要由 manifest 和 prompt 表达 |
| 发现、共识、审核、提取可衔接 | 正常路径有离线覆盖；异常恢复还需 R2 修复 |
| 错误数据在边界被阻止 | 结构校验、有界 repair、批准门槛值得保留；R1、R4 是漏掉的边界 |
| 评估能反映真实业务质量 | 尚不能充分保证；R3 会高估质量，填充率和稳定性本身也不等于值准确率 |
| 审核能追踪来源、保留历史 | queue / base / decisions 身份绑定与禁止覆盖有价值，应保留 |
| 将来增加相似 vertical | 现有文档、字段、流程模型内可配置扩展；不同采集协议或标签适配仍可能需要代码 |

Travel 无标签评估、开放 `list[object]`、rename / merge / move 的审核限制均为已记录的范围边界，不列作此次新发现的回归。需要分清“接受开放列表”与“评估时悄悄忽略其内容”：后者仍应修复。

Health feedback 会用于后续轮次，反复使用的 holdout 更接近开发验证集。最终质量应另留一组不参与 feedback 的人工核验样本；不能用适应过的验证集替代最终独立测试集。

额外可用性问题：Travel 默认 sources 只有 allianz、cover_more、scti 三家公司，而自动采样默认每类别五家公司。只使用当前采集来源时默认 discovery / loop 无法满足要求，需要显式降低 per-category。建议在页面采样预检中显示实际公司数和所需数量，避免用户先运行再遇到数量错误。

## 可以精简的地方

### S1 · 优先：退出已无运行调用的旧配置和 PDF 转换入口

`src/config.py` 为 35 行 / 27 行有效代码；`src/common/document_preprocessor.py` 为 123 行 / 92 行有效代码。通过 rg 与 AST import 扫描，src 中没有其他模块导入它们。旧 preprocessor 的测试仍存在，但测试其自身不等于当前业务流程在使用它。

旧 AppConfig 同时保留 gpt-4.1、KONKRD_LLM_* 规则；主流程使用 ModelSelection / manifest。旧 MinerU 路径也与当前 PDFingestor 主入口分离。退出候选合计 158 物理行 / 119 有效行；外部 Python 调用方和 MinerU 依赖是否仍有独立用途，删除前需确认。此次未删除。

### S2 · 优先：移除 batch 中已失效的 fallback 分流

`src/run.py` 第 392–397、444–477 行仍统计 heuristic fallback、筛选 model-only reports 并输出两套报告。当前分支始终由支持的模型 provider 构造 ExtractionResult，warnings 默认空，没有 heuristic fallback，因而两套报告的内容在正常运行下相同。

建议只聚合一次有效模型结果；旧文件名是否需要兼容单独决定，不再维持第二套计算。大约几十行是可见的精简空间，主要收益是删除一个已不存在的运行模式。

### S3 · 优先：把 extraction 文件兼容集中到一个读取边界

主 CLI 写旧 ExtractionResult，holdout 写 envelope，analysis 和 storage 分别解释格式和身份，已导致 R1。两种历史文件格式可以保留；读取后统一成一种内部记录，并共用身份校验。不要在每个消费者里增加更多 if 分支，也不需要全量迁移历史文件。

### S4 · 后续：收窄 discovery / extractor 的初始化参数

manifest 已决定 prompt、契约与 validator，但构造器仍允许重复覆盖同样信息，同时保留 model / selection / client / provider 多种配置入口。`preprocessor` 在两个构造器中只保存、未参与主流程。

建议以 manifest、model selection、schema 和必要测试注入为主入口，逐项退出无生产用途的覆盖参数。编译 schema 时返回并复用校验过的结果，避免构造器与编译器多次重复 normalize / validate。保留边界校验，削减内部重复校验；不要新增一个通用配置框架来包装现有参数。

### 不建议为了数字而做的事

不要仅按文件长度拆碎模块、压缩排版、移走代码后称作减少复杂度，或删掉 provider repair、PDF 表格解析、事务、来源校验、审核身份和必要测试。现有支撑能力占有实际代码量，若保持范围不变，靠小规模清理不足以把所有非爬虫代码减到 5,000 行。

## 代码量

统计对象为当前 Git 跟踪的 Python 文件，不包含虚拟环境、prompt Markdown、JSON 配置、数据和生成结果。

“物理行”包含空行、注释和 docstring；“有效代码行”用 tokenize / AST 排除空行、纯注释和 module / class / function docstring，按代码所占物理行计数，不是 Python 语句数。多行普通字符串仍作为程序的一部分计入。

| 范围 | 文件数 | 物理行 | 有效代码行 |
| --- | ---: | ---: | ---: |
| 全部 src | 83 | 13,470 | 11,571 |
| src 排除 scraper | 75 | 11,576 | 9,894 |
| schema + refine + schema_application + verticals | 35 | 5,051 | 4,352 |
| 上述四包 + 主 CLI run.py | 36 | 5,742 | 4,970 |
| tests，单独统计 | 46 | 8,357 | 7,147 |
| 全部跟踪 Python，含 tests | 129 | 21,827 | 18,718 |

四个核心包的小计包括 Canonical 业务编译和审核 UI，但不含 common、PDFingestor、主操作 UI、storage、Health labelled evaluation、cost、stability 等。它是明确文件集合的小计，不能宣称代表所有 Python 业务代码。

src 的主要分布：

| 目录 | 有效代码行 |
| --- | ---: |
| refine | 2,333 |
| scraper | 1,677 |
| common | 1,440 |
| schema | 1,119 |
| src 根目录入口与模型等 | 905 |
| storage | 865 |
| PDFingestor | 805 |
| schema_application | 664 |
| evaluation | 595 |
| tool_ui | 470 |
| stability | 280 |
| verticals | 236 |
| cost | 182 |

所以：若 5,000 指四个核心包加 CLI 的有效代码，当前接近该估算；若指排除 prompt 和爬虫后整个可运行项目，当前超过该数。行数不是此次修复与验收标准。

## 验证及下一步

- `.venv/bin/python -m compileall -q src tests` 通过。
- `env -u KONKRD_TEST_DATABASE_URL .venv/bin/python -m unittest discover -s tests`：388 项，387 通过、1 项真实 PostgreSQL 测试跳过。
- R1、R2、R3 均以现有模块和测试 fixture 离线复现；R4 使用临时生成的真实 PDF 与 fake provider 复现。
- 另用程序生成含标题与带框线表格的 PDF，真实 PDFingestor 保留了标题、服务名称和数值，文本 / 表格基本冒烟通过。这不覆盖真实保险文档复杂排版或扫描件。
- 没有调用付费模型、抓取网站、读取实际保险 PDF 或标签原数据、连接真实数据库。本次也未做新一轮真实浏览器或依赖漏洞扫描。

建议顺序：先分别修复 R1–R4 并补行为测试；再退出旧入口、fallback 分流和重复格式判断；最后用每个 vertical 的小规模真实样本，对关键字段、缺失、错误值、产品身份与来源做人工核验。将问题修复和行为保持的精简分成独立变更，避免一次大改后无法定位质量变化的原因。
