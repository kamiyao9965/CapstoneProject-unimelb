# Spec：Health / Travel 统一流程（精简版）

状态：实施中；进度和验证记录见 [todo](../../tasks/todo.md)。
日期：2026-09-08。用户：项目组，用于开发、实验、审核与演示。

## 1. 目标与范围

将 Health、Travel 合入同一个 main，并用同一套代码完成：PDF 选样 → schema 发现 → 候选共识 → 人工审核 → 抽取 → 可用的分析。UI 全局选择 vertical，manifest 和 prompt 表达领域差异。

- 保留现有 Streamlit、CLI、模型接入、PDF 解析和已支持的周边能力，不重建平台。
- **约 5,000 行只是用户对核心逻辑规模的直觉估计，不是上限或验收门槛。** 目标是避免过度设计，优先删除重复与无调用的旧实现。
- 讨论核心逻辑体量时不把 prompt 正文和爬虫混入；不限制 prompt 长度。prompt 加载、拼装、参数验证仍属于实际程序逻辑。
- 不做 aliases 配置、别名管理和同义词归并；历史数据中的 aliases 可以只读保留。
- 第三个 vertical 的方向、配置、prompt、样本和零代码接入实验全部延期，等待用户后续决定。

本规格替代上一版较重的框架设计；实施顺序合并在本文，不新增第三份 plan 文件。

## 2. 用复杂度约束设计，不用行数约束实现

- 一个操作只有一份业务实现；UI/CLI/manifest 不分别维护领域判断、默认值和校验。
- 一个概念只有一个权威来源；不因“将来可能扩展”增加注册平台、配置层或通用框架。
- 新抽象必须解决 Health/Travel 已存在的重复或差异；只有一个调用方且无独立职责时，优先用现有模块中的小函数。
- 兼容范围以真实使用证据为准；不长期维护两套完整流程，也不静默破坏正在使用的旧数据。
- 保留正确性校验、错误处理、可读性和必要测试；不为减少行数压缩排版、重写稳定底层组件或删功能。

行数只用于观察改造结果，可在收尾报告一次同口径前后变化；不分配逐模块额度、不建设专门的 LOC 管理工具，不因超过 5,000 行阻止交付。

当前参考：src 约 12,193 行有效代码，其中纯 prompt 约 108、爬虫约 1,677；仅扣除这两项后约 10,408 行，仍包含 PDF、模型、数据库等支撑代码。该数字不能与用户估计的“核心逻辑”直接比较，也不代表其中都值得重写。

## 3. 只保留必要的设计

### 3.1 一个 manifest，加三份 prompt

```text
configs/<vertical>/
  manifest.json
  prompts/discovery.md
  prompts/patch.md
  prompts/extraction.md
```

Travel 现有 sources、批准 Canonical Schema 与存储配置继续保留。公共契约仍在 contracts；不会为每个领域复制整套契约或引擎。

扩展现有 VerticalManifest，集中处理名称、能力、文档/产品分类、采样、路径、prompt 引用和少量共识参数。registry 扫描有效配置包并加载文本；CLI/UI 复用它。保留一处旧命令默认映射即可，**不新增 application.json、独立上下文框架、插件系统或可执行规则语言**。

配置每次运行解析一次，传递已验证的 manifest/必要参数；缺 prompt、未知能力、路径越界和领域冲突在外部调用前失败。prompt 相对配置包，数据/输出路径沿用明确的项目路径及环境/CLI 覆盖规则。

### 3.2 一个公共字段模型，少量确定性规则

内部统一 fields、产品分类、身份字段和“集合名→条目”的 taxonomies。Health 的 hospital/extras 与 Travel 的 coverage 都是数据；分类字段只出现一次。文档类型、抽样类别、产品类型分开。

共享函数完成枚举、字段唯一、applies_to、身份引用和 single/multiple 校验；manifest 只提供参数。发现仍生成业务字段，不能变成手工编写完整抽取 schema。保留现有 list[object] 的能力限制。

先让现有两个 JSON schema 经一个入口进入公共内存表示；只在必须改变持久格式时升级受影响契约。**不预建整套 manifest v2/schema v2 注册体系或强制升级所有 artifact envelope。**

### 3.3 最小兼容，不长期运行两套实现

保留当前实际使用的两个 JSON 格式及已批准 Canonical；旧形状转换集中在一个小入口。需要持久化转换时写新文件、保留源文件和来源信息，不覆盖生产 schema 或更改批准哈希。

无生产调用的旧 loader、静态领域模型和重复验证器，在核对公开接口后删除。旧 YAML/entity-v2 只在确有使用证据时保留最小入口，不主动建设通用迁移系统。旧未完成审核不能被新流程静默接管：身份不一致时明确停止，原记录保留，必要时显式转换或用原版本完成。

取消 aliases 文件读取、同义词归并和新 add_alias 提案；普通合法字段名校验保留。旧 add_alias 操作只读可审计，不伪装为已执行；旧 --alias-config 明确报不支持。取消归并引起的票数变化用固定样例说明。

### 3.4 一个执行流程，一个薄 UI

复用现有 run.py、refine/loop.py 和 SchemaExtractor。核心代码不按 vertical 名称切换另一套实现；公共能力调用既有 provider/PDF/storage 模块。

UI：选择 vertical → 查看可用操作 → 输入文档/schema → 执行 → 查看/审核结果。领域持续可见，表单、确认、队列和结果按领域/运行隔离；结果归属采用启动时的参数。UI 不复制业务规则，不建设新的任务调度器、历史数据库或运行快照框架。

使用现有 run_id、schema 版本和审核元数据检查身份；确有缺失时只补受影响的契约和消费者，不为增加一套全局指纹体系改写所有产物。

## 4. 完整边界检查表

沿用上版 20 项检查范围，但合并实现、减少框架。T01 从公开入口追踪调用链并补遗漏；每项记录最终唯一来源、删除项或具名保留理由。

| ID | 当前需要检查的代码 | 收紧后的唯一归属 | 任务 |
| --- | --- | --- | --- |
| B01 领域列表/默认值 | verticals、tool_ui 中重复列表 | registry + 现有默认选择入口 | T03、T16 |
| B02 配置解析 | run、refine 各自回退 | 现有 VerticalManifest，一次解析 | T03、T11 |
| B03 prompt | schema/prompts、schema_application/prompts、registry | 配置包正文 + 一个加载器 | T03–T05、T18 |
| B04 契约加载 | common/json_contracts 的领域/公共别名 | 原 contract loader，按实际需要共享契约 | T06、T07 |
| B05 schema 形状 | product_type_field 与三种 taxonomy 键 | schema 的公共内存模型 | T06–T09 |
| B06 三种分类 | sampler、steps、manifest categories | manifest 分开声明，sampler 只选样 | T03、T08、T11 |
| B07 校验/身份 | schema/validation、verticals/travel_insurance、canonical | 共享校验函数，分类/保护参数来自配置 | T06、T09、T15 |
| B08 输出基数 | contract、SchemaExtractor | 同一个 single/multiple 编译器 | T09 |
| B09 路径/覆盖规则 | run batch/output、config、data_paths | 既有路径边界，不在调用方拼默认值 | T03、T08、T09 |
| B10 PDF 缓存 | PDFingestor/adapter 默认落 Health | adapter 接收解析后的路径，解析器保留 | T09 |
| B11 共识策略 | steps/cli/renderer 的领域分支 | 少量 manifest 参数 + 同一 aggregator/apply | T10–T12 |
| B12 aliases | normalizer、consensus、add_alias | 删除运行依赖；旧数据只读 | T10–T12、T18 |
| B13 审核/恢复 | queue/decisions/apply/rounds | 同一身份检查和 apply，不做通用历史迁移 | T11、T12、T17 |
| B14 通用分析 | schema_application/analyze | 公共字段 + 可信样本分类 | T13 |
| B15 Health 标签指标 | evaluation/metrics、run 分支 | 既有 evaluation 能力集中保留，入口不识别领域名 | T13 |
| B16 稳定性/成本/日志 | stability、cost、公共 usage | 公共字段比较、统一路径、原日志实现 | T14 |
| B17 Canonical/存储 | Travel builder、canonical_review_app、storage | 共享候选/编译入口，批准契约仍唯一权威 | T15、T17 |
| B18 UI 状态/操作 | tool_app、forms、commands、两个审核页 | 领域优先的薄表单与 session 状态 | T16、T17 |
| B19 旧模型/入口 | schema/loader、validator、models、pipeline wrapper | 有使用证据才保留最小兼容入口 | T01、T07、T18 |
| B20 采集/文档 | scraper/travel、registry、README、architecture | 原爬虫保留，统一能力入口和文档 | T16、T19、T20 |

允许的领域专属代码仅为已证明需要的旧格式读取、Health 标签数据/指标和 Travel 网页采集；不得把发现/抽取分支搬到“adapter”目录后宣称统一。不改造底层组件不代表可以跳过其调用边界检查。

## 5. 保留的行为与不做事项

- Health 输出单产品并保留现有评估；Travel 输出多个产品并保留采集、Canonical 编译和入库，评估仍禁用。
- Canonical 候选使用已有明确映射/批准契约信息，未知字段需审核；不增加新 mapping-profile 平台，不自动批准或改变现有表结构。
- 保留产品身份唯一、内容去重、holdout 隔离、有限 repair、费用记录、失败停止、成功文件不覆盖。
- 评估分母使用可信分类，不采用模型预测；没有适用样本为 N/A，不将无标签 Travel 声称为准确率已验证。
- 不增加第三领域、别名系统、认证部署、多租户、任意嵌套 schema、通用数据映射 DSL，也不为凑行数重写稳定的底层组件。

## 6. 技术约定与命令

沿用 requirements.txt 和项目 venv：Python 3.10–3.13、Streamlit、jsonschema、已有模型/PDF/PostgreSQL 依赖。本轮不新增依赖。

src/schema、refine、schema_application、verticals、tool_ui 保持现有职责；common 复用 JSON、模型、路径和日志。tests 使用 unittest，spec 在 docs/specs，任务在 tasks/todo.md。不依据过期文档重建 src/extract。

风格沿用类型注解、小函数和 snake_case；示例来自现有 VerticalManifest：

```python
def supports(self, capability: str) -> bool:
    return bool(self.capabilities.get(capability, False))
```

代码检查点执行：

```bash
.venv/bin/python -m compileall src tests
env -u KONKRD_TEST_DATABASE_URL .venv/bin/python -m unittest discover -s tests
.venv/bin/python src/run.py --help
.venv/bin/python src/refine/loop.py --help
git diff --check
```

UI：`.venv/bin/python -m streamlit run src/tool_app.py`。无独立 build/lint 脚本时，不把 compileall 描述成打包验证。

测试优先复用现有套件，按 Health/Travel 参数化，补真实缺口：混合领域输入、single/multiple、缺 prompt、关闭能力、审核恢复、alias 退出、配置/输出路径和 UI 切换。检查点用离线 fake provider/runner，不能调用真实模型或数据库。

真实浏览器检查主 UI、审核往返和错误态；有授权及样本时分别运行两领域发现/抽取并人工核对。示例：

```bash
.venv/bin/python src/run.py discover --manifest configs/private_health/manifest.json --per-category 1 --seed 42 --output outputs/private_health/smoke/schema.json
.venv/bin/python src/run.py discover --manifest configs/travel_insurance/manifest.json --per-category 1 --seed 42 --output outputs/travel_insurance/smoke/schema.json
```

执行前选新输出路径、核实样本和费用范围，实际 extract/review 命令记录真实产物路径。storage 读写若改变，使用获授权的可丢弃测试库执行 `.venv/bin/python -m unittest tests.test_postgres_storage_live -v`。离线、浏览器、模型、DB 分别记录，未运行不得算通过。

## 7. 实施与完成标准

顺序：T01–T02 审计和现有工作合入 main → T03–T09 单一配置/字段模型与发现抽取 → T10–T15 共识审核及剩余边界 → T16–T18 UI/清理 → T19–T20 验收和统一改造合入 main。按运行切片实施，不新增独立“平台建设”阶段。

| ID | 验收条件 |
| --- | --- |
| AC01 | 两领域现有工作和统一改造最终都在远端 main，有提交证据 |
| AC02 | B01–B20 及新发现边界有唯一 owner/保留理由，核心无另一套领域实现 |
| AC03 | 重复领域分支/默认值/规则有实际减少，新增抽象能说明当前用途；无为未来领域预建的平台或长期双实现 |
| AC04 | Health/Travel 发现→共识→审核→抽取验证完成，单/多产品含义正确 |
| AC05 | UI 的领域、能力、确认、审核队列与结果隔离正确 |
| AC06 | 无 aliases 配置读取/管理/语义归并，旧信息只读可追踪 |
| AC07 | 必须保留的旧 JSON/批准契约可用；无自动覆盖、降级校验或伪造批准 |
| AC08 | 现有 Health 评估、Travel 采集/存储保留；第三领域未加入，验证限制明确 |

始终：先 rg 找调用，使用 venv，保留用户变更，检查完整删除项，修改相关测试和文档。额外新依赖、超范围 DB/生产数据变化、历史成功文件覆盖需已有明确授权；已经授权的动作不重复询问。禁止提交 .env、PDF、标签原数据、输出或 usage log，禁止为减少行数删除正确性保障。

现有基线快照：HEAD 012fdfe、main 24f31ee，差距 43 提交；前序 370 项测试通过、1 项跳过。合并前重新核实远端、187 文件差异中的旧数据删除及 src/__init__.py 空白问题。缺失的 project-index/coding-standards/agent-workflows/review-checklist 文档不作为新建四套流程的理由，更新真实 README/architecture 即可。

本轮最大风险是借统一之名增加框架、历史兼容变成第二套引擎、UI 只改标签却复用旧状态；分别通过抽象用途审查、最小兼容样例和交互测试验收。第三领域方向不阻塞本轮；真实冒烟样本/预算在执行前落实。

[Todo](../../tasks/todo.md) 当前位于被 Git 忽略的 tasks 目录，实施 PR 只明确纳入本文件，不批量提交本地任务资料。
