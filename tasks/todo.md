# TODO：Health / Travel 统一流程（精简版）

规格：[multi-vertical-engine-spec.md](../docs/specs/multi-vertical-engine-spec.md)。状态：实施中。

**约 5,000 行只是规模估计，不设行数上限、逐模块额度或 LOC 验收关卡。** 本轮减少重复和不必要抽象，保留正确性；prompt 正文和爬虫不混入核心逻辑体量。第三领域与 aliases 功能不做。

## 执行方式

沿用现有模块和测试，按实际运行路径改造。下列 20 项取代原 31 项任务；取消独立上下文框架、application.json、全产物版本升级、通用迁移/映射平台和行数管理工具。

每项预计最多 5 个实现/测试文件（S：1–2；M：3–5）；若发现更多独立工作先拆分，不塞入一个大重构。过渡读取/导出只能为当前调用方服务，并在 T18 收口。不要为了照表执行而新建已有模块能承担的类或文件。

**V0：** 代码检查点和合并前从根目录执行：

```bash
.venv/bin/python -m compileall src tests
env -u KONKRD_TEST_DATABASE_URL .venv/bin/python -m unittest discover -s tests
git diff --check
```

**VCLI：** 修改 CLI 时执行 `.venv/bin/python src/run.py --help` 和 `.venv/bin/python src/refine/loop.py --help`。

targeted unittest 命令均以 `.venv/bin/python -m unittest` 开头，离线运行。文件列表包含拟新增文件；测试只补行为缺口，不要求镜像实现或强行保持旧测试数量。检查点核验证据，不默认重复请求许可。

依赖主线：T01–T02 审计/已有工作合入 main → T03–T09 配置/模型/发现抽取 → T10–T15 共识审核/剩余边界 → T16–T18 UI/清理 → T19–T20 验收/最终合入。

## A. 审计与现有工作合入 main

### T01 — 找出应保留、合并和删除的代码

- [x] 完成 T01。
- **目的：** 重新核对 main 差异与 spec B01–B20，从真实调用链判断删减空间。
- **验收：** 每项边界有唯一 owner/处置；旧数据和模块删除有替代路径或理由；新增抽象必须对应当前重复，不按假设中的第三领域设计。
- **验证：** `git ls-remote origin refs/heads/main refs/heads/feat/travel-insurance`、`git diff --name-status main...HEAD`、`git diff --check main...HEAD`；rg 搜索领域常量/默认路径/aliases/旧 loader 后追踪调用方。
- **依赖：** 无。**规模：** S。**文件：** spec、本 todo。

### T02 — 验证并合入现有 Health / Travel

- [x] 完成 T02。
- **目的：** 建立包含两领域的 main 基线，再做统一改造。
- **验收：** 两领域现有抽取/审核基线通过；处理已知空白问题，较大回归另拆修复；按仓库规则合并并核验远端提交，不强推或覆盖用户变更。
- **验证：** V0、VCLI；`.venv/bin/python -m unittest tests.test_run tests.test_loop tests.test_travel_schema_migration tests.test_canonical_storage -v`；合并后读取远端 main 并核实提交包含关系。
- **依赖：** T01。**规模：** M。**文件：** `src/__init__.py`、`tests/test_run.py`、`tests/test_loop.py`、本 todo；另执行 Git 操作。

**检查点 A：**

- [x] 基线合并、删除项和回归证据齐全；这不是统一改造已完成的声明。

## B. 用现有模块统一配置和字段模型

### T03 — 一个配置解析和 prompt 加载入口

- [x] 完成 T03。
- **目的：** 扩展现有 manifest/registry，集中领域发现、能力、默认值、路径和文本加载。
- **验收：** CLI/UI 可复用同一目录与解析结果；文档/抽样/产品分类分开；缺资源、冲突、越界在外部调用前失败，不新增上下文框架或应用配置层。
- **验证：** `.venv/bin/python -m unittest tests.test_vertical_manifest tests.test_tool_ui -v`；两份 manifest 的有效配置、重复 code、缺 prompt 和路径覆盖负例。
- **依赖：** T02。**规模：** M。**文件：** `src/verticals/manifest.py`、`src/verticals/registry.py`、`src/common/data_paths.py`、`contracts/vertical_manifest.schema.json`、`tests/test_vertical_manifest.py`。

### T04 — Health prompt 外置

- [x] 完成 T04。
- **目的：** 把 Health 三阶段知识移入一个配置包，代码只加载文本。
- **验收：** 正文有单一来源；manifest 参数保留 Health 行为；格式调整与外置的差异明确记录，不意外改抽取要求，未迁移调用方只留具名过渡导出。
- **验证：** `.venv/bin/python -m unittest tests.test_discovery tests.test_consensus tests.test_extractor -v`；请求文本前后对照，确认实际使用文件内容。
- **依赖：** T03。**规模：** M。**文件：** Health manifest、三个 `configs/private_health/prompts/*.md`、`src/schema/prompts.py`（共 5 个）。

### T05 — Travel 复用同一 prompt 加载

- [x] 完成 T05。
- **目的：** 用同一机制接入 Travel，保留产品分类和多计划要求。
- **验收：** 不增领域专用加载器；三个 prompt 与 manifest 引用一致；采集/批准 schema/存储配置保留，重复正文在 T18 清除。
- **验证：** `.venv/bin/python -m unittest tests.test_travel_schema_migration tests.test_travel_consensus tests.test_vertical_manifest -v`；请求文本对照。
- **依赖：** T04。**规模：** M。**文件：** Travel manifest、三个 `configs/travel_insurance/prompts/*.md`、`src/schema_application/prompts.py`（共 5 个）。

**检查点 B：**

- [ ] V0 通过；两领域确实使用同一配置/文本入口，没有增加独立插件或版本平台。

### T06 — 统一公共字段模型和校验

- [ ] 完成 T06。
- **目的：** 将两领域的字段、产品身份、taxonomies 和输出基数交给共享函数处理。
- **验收：** 分类字段不重复，taxonomy 名称来自数据；enum/applies_to/身份/类型校验保留；只更新必需契约，不连带升级全部 envelope。
- **验证：** `.venv/bin/python -m unittest tests.test_schema_validation tests.test_extraction_contract tests.test_json_contracts -v`；单/多产品、重复字段和非法身份引用负例。
- **依赖：** T05。**规模：** M。**文件：** `src/schema/validation.py`、`src/schema/contract.py`、`src/common/json_contracts.py`、必需的一份公共发现契约、`tests/test_schema_validation.py`。

### T07 — 收拢必要兼容，清理无调用旧模型

- [ ] 完成 T07。
- **目的：** 在一个入口读取实际使用的旧 JSON，避免保留第二套领域模型和编译器。
- **验收：** 两旧 JSON 经原验证后进入公共表示；原文件/批准状态不变；旧 YAML/entity-v2/静态模型有使用证据才留最小兼容，不建设通用迁移系统。
- **验证：** `.venv/bin/python -m unittest tests.test_full_pipeline_adapter tests.test_travel_schema_migration tests.test_canonical_schema -v`；原始文件不变、未知结构拒绝及调用方扫描。
- **依赖：** T06。**规模：** M。**文件：** `src/schema/loader.py`、`src/schema/validator.py`、`src/models.py`、`tests/test_full_pipeline_adapter.py`、`tests/test_travel_schema_migration.py`。

### T08 — 两领域发现走同一流程

- [ ] 完成 T08。
- **目的：** 让 discovery 从公共配置获得 prompt、契约、路径和抽样设置。
- **验收：** 无领域专属发现实现；采样保持内容去重/holdout 隔离；请求契约与正文一致，失败和 repair 上限保留。
- **验证：** `.venv/bin/python -m unittest tests.test_discovery tests.test_sampler tests.test_run tests.test_structured_output -v`；VCLI。
- **依赖：** T07、T03。**规模：** M。**文件：** `src/schema/discovery.py`、`src/schema/sampler.py`、`src/run.py`、`tests/test_discovery.py`、`tests/test_run.py`。

**检查点 C：**

- [ ] V0、VCLI 通过；公共模型保留两领域语义，旧 loader 不再构成第二份业务权威。

### T09 — 两领域抽取复用同一 compiler/extractor

- [ ] 完成 T09。
- **目的：** 统一单份/批量抽取的契约、路径和 PDF 业务适配，保留底层解析器。
- **验收：** Health 单产品、Travel 多产品与身份校验正确；跨领域 schema 在调用前失败；缓存不默认落 Health，错误不写成成功结果。
- **验证：** `.venv/bin/python -m unittest tests.test_extractor tests.test_extraction_contract tests.test_document_preprocessor tests.test_run -v`；批量中途失败、缓存路径和同名文档负例。
- **依赖：** T08。**规模：** M。**文件：** `src/schema_application/extractor.py`、`src/schema/contract.py`、`src/PDFingestor/adapter.py`、`tests/test_extractor.py`、`tests/test_document_preprocessor.py`。

## C. 共识、审核和剩余边界

### T10 — 取消 aliases 与重复候选处理

- [ ] 完成 T10。
- **目的：** 保留确定性的命名校验和投票，退出别名读取/归并及新 add_alias 提案。
- **验收：** 正常运行不依赖 aliases.json；不同名称不被同义词表合并，票数变化有对照；旧 alias 操作只读审计，不误标已应用。
- **验证：** `.venv/bin/python -m unittest tests.test_normalizer tests.test_patch tests.test_aggregator -v`；mock 文件读取及同名/不同名/重复票负例。
- **依赖：** T09。**规模：** M。**文件：** `src/refine/candidates/normalizer.py`、`src/refine/candidates/patch.py`、`src/refine/candidates/aggregator.py`、`tests/test_normalizer.py`、`tests/test_patch.py`。

### T11 — 共识与恢复用少量配置参数

- [ ] 完成 T11。
- **目的：** 合并 loop/consensus 的重复默认值、领域判断和阶段调用。
- **验收：** runs、保护字段和晋升策略来自 manifest；文档类别不当作产品类型；恢复验证原 schema/queue/领域身份，旧 --alias-config 明确报不支持。
- **验证：** `.venv/bin/python -m unittest tests.test_consensus tests.test_loop tests.test_travel_consensus -v`；VCLI；修改参数、关闭 evaluation、混合领域恢复负例。
- **依赖：** T10。**规模：** M。**文件：** `src/refine/consensus.py`、`src/refine/pipeline/cli.py`、`src/refine/pipeline/steps.py`、`src/refine/pipeline/rounds.py`、`tests/test_loop.py`。

**检查点 D：**

- [ ] V0 通过；共识差异能从配置解释，alias 退出不依靠静默忽略输入实现。

### T12 — 一个审核队列与应用实现

- [ ] 完成 T12。
- **目的：** 让两个领域复用接受、拒绝、编辑和应用逻辑。
- **验收：** queue/decision/base 身份检查一致；保留自动共识基线，pending/reject 不应用；不支持的旧操作明确停止，不自动转换所有历史运行。
- **验证：** `.venv/bin/python -m unittest tests.test_review tests.test_renderer tests.test_loop -v`；保护字段、混合队列、缺 decision 和旧 alias 操作负例。
- **依赖：** T11。**规模：** M。**文件：** `src/refine/human_review/queue.py`、`src/refine/human_review/decisions.py`、`src/refine/human_review/apply.py`、`src/refine/artifacts/schema_fields.py`、`tests/test_review.py`。

### T13 — 分析共用规则，保留已有 Health 指标

- [ ] 完成 T13。
- **目的：** 共享字段适用性/填充率分析，集中调用现有标签评估能力。
- **验收：** 分母来自可信分类，无样本为 N/A；公共 CLI 不识别领域名称来决定评估；Health 指标保留，Travel 无标签时不可评估，不造通用数据映射 DSL。
- **验证：** `.venv/bin/python -m unittest tests.test_analyze tests.test_pipeline_accuracy_improvements tests.test_run -v`；错分类、无适用样本和禁用能力测试。
- **依赖：** T12。**规模：** M。**文件：** `src/schema_application/analyze.py`、`src/evaluation/metrics.py`、`src/verticals/registry.py`、`src/run.py`、`tests/test_analyze.py`。

### T14 — 稳定性、成本入口复用字段与路径

- [ ] 完成 T14。
- **目的：** 补齐辅助 CLI 的 Health 默认值和固定 taxonomy 判断。
- **验收：** signature 遍历公共字段/taxonomies；measure/compare/cost 使用一致路径；保留原 usage 实现，不新建日志或运行历史平台。
- **验证：** `.venv/bin/python -m unittest tests.test_stability tests.test_standalone_cli -v`；三个辅助入口分别执行 `--help`。
- **依赖：** T13。**规模：** M。**文件：** `src/stability/signature.py`、`src/stability/measure.py`、`src/stability/compare.py`、`src/cost/estimate.py`、`tests/test_stability.py`。

**检查点 E：**

- [ ] V0 通过；主流程和辅助入口无遗漏的 Health 回退或错误路径，未扩大评估/日志范围。

### T15 — Canonical 与存储保持一个业务边界

- [ ] 完成 T15。
- **目的：** 复用现有候选/编译/入库函数，删除 Travel 编排特判，不重写数据库层。
- **验收：** 映射来自已有明确配置/批准信息，未知字段需审核；批准契约仍唯一权威；身份唯一、幂等和失败回滚保留，不新增 mapping-profile 平台或 DB 表。
- **验证：** `.venv/bin/python -m unittest tests.test_canonical_schema tests.test_canonical_storage tests.test_storage_service -v`；候选不可入库、批准内容不变、跨领域身份拒绝。
- **依赖：** T12、T09。**规模：** M。**文件：** `src/schema/canonical.py`、`src/verticals/travel_insurance.py`、`src/storage/service.py`、`tests/test_canonical_schema.py`、`tests/test_storage_service.py`。

## D. 薄 UI 与清理

### T16 — 主 UI 全局选择 vertical

- [ ] 完成 T16。
- **目的：** 一个工作台按配置显示领域、能力、默认参数和结果。
- **验收：** 领域列表不再重复维护；切换时隔离输入/确认/结果，运行按启动参数归属；命令构造复用现有 CLI，不新建服务平台或任务调度器。
- **验证：** `.venv/bin/python -m unittest tests.test_tool_ui tests.test_tool_app -v`；fake runner 连续切换两领域/操作，检查禁用能力不执行。
- **依赖：** T14、T15。**规模：** M。**文件：** `src/tool_app.py`、`src/tool_ui/forms.py`、`src/tool_ui/commands.py`、`tests/test_tool_ui.py`、`tests/test_tool_app.py`。

### T17 — 审核页复用同一领域身份

- [ ] 完成 T17。
- **目的：** 主 UI 与字段/Canonical 审核页正确衔接，保持薄呈现。
- **验收：** 页面显示原运行的 vertical/schema；错误领域队列和旧确认不能提交；执行既有公共 apply/approve，写新文件，候选预览不批准。
- **验证：** `.venv/bin/python -m unittest tests.test_review_app tests.test_travel_canonical_review -v`；用 fixture 检查审核往返、错误态和状态隔离。
- **依赖：** T16、T12。**规模：** M。**文件：** `src/refine/human_review/ui.py`、`src/review_app.py`、`src/canonical_review_app.py`、`tests/test_review_app.py`、`tests/test_travel_canonical_review.py`。

**检查点 F：**

- [ ] V0 通过；UI 不止切换标签，表单、操作、审核身份和结果真正一致。

### T18 — 清除过渡实现与退出资源

- [ ] 完成 T18。
- **目的：** 删除已无调用的别名资源、重复正文和临时兼容分支。
- **验收：** 两份 aliases.json 及读取依赖退出；prompt 正文只在包中存在；B01–B20 的残留均有真实使用理由，不能用“未来扩展”解释冗余。
- **验证：** V0；rg 复查 aliases.json/load_alias_config、领域条件分支、prompt 常量及旧 loader 调用；逐项登记保留/删除结果。
- **依赖：** T17。**规模：** M。**文件：** 两份 `configs/*/aliases.json`、`src/schema/prompts.py`、`src/schema_application/prompts.py`、`src/refine/candidates/normalizer.py`（共 5 个）。其他独立残留按 T01 清单单独处理。

## E. 验收与最终合并

### T19 — 验证完整流程及简化效果

- [ ] 完成 T19。
- **目的：** 证明两领域正确运行且结构更简单，不以行数或目录重排代替功能证据。
- **验收：** 离线覆盖发现→共识→审核→抽取及跨领域负例；真实浏览器验证切换/审核/错误态，已授权时用真实 PDF 核对单/多产品；记录删除的重复逻辑与新增抽象用途，行数仅可选附报，不设 5,000 行关卡。
- **验证：** V0、VCLI；`.venv/bin/python -m unittest tests.test_multi_vertical_flow -v`；按 spec 运行浏览器/已授权模型检查，storage 读写变化时另跑可丢弃 DB 集成测试。未运行分别标注，不能算通过。
- **依赖：** T18。**规模：** M。**文件：** `tests/test_multi_vertical_flow.py`、`tests/test_tool_app.py`、`tests/test_loop.py`、spec、本 todo。

### T20 — 更新真实入口文档并合入 main

- [ ] 完成 T20。
- **目的：** 将统一改造交付到 main，保留简短可复现的使用说明。
- **验收：** README/architecture 与实际模块和无 aliases 工作流一致；完整 diff、删除项和验收结果已审查；按仓库规则合入并核实远端提交，不创建第三领域或额外流程文档。
- **验证：** V0、VCLI；最终 diff 检查、远端 main 提交包含关系；逐项核对下方 AC。
- **依赖：** T19。**规模：** M。**文件：** `README.md`、`docs/architecture.md`、spec、本 todo；另执行 Git 操作。

## 完成清单与证据

- [ ] AC01：现有工作和统一改造均进入远端 main。
- [ ] AC02：所有已发现边界有唯一 owner 或具名保留理由。
- [ ] AC03：减少重复/无用抽象，没有为凑行数或未来领域制造新复杂度。
- [ ] AC04：两领域完整流程与单/多产品结果通过验证。
- [ ] AC05：UI 领域、能力、确认、队列、结果隔离正确。
- [ ] AC06：无 aliases 新功能与运行依赖，旧信息只读可追踪。
- [ ] AC07：必要旧格式/批准契约可用，无静默覆盖或降级验证。
- [ ] AC08：现有周边能力保留；第三领域未加入，各层验证限制如实记录。

完成任务时追加实际提交与验证结果再勾选，不以历史测试代替本轮证据：

| 任务 | 提交/实际变更 | 验证结果及限制 | B/AC |
| --- | --- | --- | --- |
| T01–T02 进行中 | 远端核实：main 24f31ee，Travel 012fdfe，43 提交；独立基线审查 | 初始 371 tests；修复批处理失败退出码、Health 平铺字段与旧标签精确对齐后 373 tests（372 pass / 1 DB skip），compileall 和两个主 CLI help 通过 | B19/B20 |

第三领域方向和零代码接入实验另行决定，不阻塞本轮。不为尚未确定的领域提前增加抽象。

注意：tasks 目录被 Git 忽略，实施提交时只明确纳入本 todo，不批量加入其他本地记录。

基线完成记录：2026-09-08 远端 main 已核实为 063c13e，包含原 Travel 43 提交及回归修复。374 tests（373 pass / 1 DB skip），独立审查另跑22项全部通过。旧 ingestor/router→PDFingestor；旧 LLMExtractor→SchemaExtractor；旧爬虫配置→Travel sources。旧占位领域/YAML无当前生产调用；6份旧 CSV 在默认外置数据根未找到，不能称为已验证搬迁，历史 Git 仍保留；真实 Health 标签批处理未验证。

T03–T05：manifest 自动发现，分离产品类型/采样类型、声明共识策略；六份 prompt 原文外置，单一加载入口，缺失/空文件/路径越界提前拒绝。377 tests（376 pass / 1 DB skip）、compileall/diff-check通过；旧常量暂时只重导出，T18移除。
