# 车险开发集解析修复（2026-10-02）

## 第四轮：概览索引、质量拦截与跨页证据（2026-10-03，最新）

最终产物：`outputs/car_insurance/parse_check_v8/`；v7 为本轮中间结果，旧产物未覆盖。
解析器缓存版本 `pdfingestor.v9`。5 份开发文件 289 页已重跑；holdout/test 未读取或调优。

### 实际修复

- 综合险第 5 页：恢复两列保障名称—参考页码，15 个保障条目和 2 行分组标题（共 17 行）。
  已对照页面截图核实，Optional cover / pay extra 分组没有降为普通包含保障。
  该输出是概览索引，不将本页符号当作独立、完整的承保结论；详细条款仍是限额和条件的依据。
- 不同表结构走不同分支：该页的两列索引不再被强行解释成第三方险的双产品四列表。
- 新增明确续页标题的来源链接：保留原文，不跨页复制/猜测限额。AAMI 第三方险 33→34 页 Substitute car 已建立链接。
  当前共 16 个明确续页标题链接；Youi 另有 11 个无法唯一匹配的标题，仍明确标为 unresolved，需复核。

### 质量控制（不能等同于全部内容修好）

- 未恢复的概览表、未建立状态映射的重复图形、孤立的 We/cover、空页、UNRESOLVED 和未解决的续页关联进入检查。
- Car discovery、consensus patch 和 extraction 的共享 Markdown 输入路径开启 `enforce_quality`。
  有未解决警告或上述问题时抛出 `ParserQualityError`，不调用模型；本地 Markdown 预览仍可保存。
  Health/Travel 不强制启用这一新门槛；其他调用者的默认行为未改。此门槛不是通用模型请求防火墙。
- 新增集成测试直接验证 car discovery/extraction 在警告存在时 provider.requests 为空。
- 检测具有启发性：没有检测到阻断项仅表示 `no_detected_blockers_not_human_approved`，不是审核通过或准确率保证。
- 当前没有伪造人工批准，也没有通过清空警告绕过门槛；需要修复问题后重跑。对确认无害的警告，后续应设计与 PDF/hash/解析器配置绑定的人工放行流程，当前尚未实现该流程。

### 金额、单位与状态

`quality_review.json` 为每份文件记录 PDF hash、解析器配置 hash、阻断项、续页链接和带原文/块 ID 的审核线索。
金额、天数、per day/item/claim/incident/policy、excess、Limited/Optional/Not required 会被列出。
侧栏分区也进入检查清单，以便核查边界是否将条件分到错误保障；没有声称几何边界已普遍可靠。
这些只是需复核的来源线索，不是已验证的数值抽取。提示词强调 Limited 与 Not required 不得随意合并状态，
概览不得覆盖详细条件，schema 无法表达时使用已有 notes 记录限制，不新增未声明字段。
模型语义正确性仍需要后续人工标注与评估，不能凭提示词修改声称解决。

当前带阻断项的页数：AAMI 综合 34、AAMI 第三方 33、QBE 综合 4、QBE 第三方 3、Youi 14，共 88 页。
更多警告是检测更保守的结果，不是宣称新增了 88 页解析错误；5 份文档当前均未自动放行。
仍需逐项区分真实错误、可接受降级和误报。**因此此次未启动 schema discovery。**

验证：146 项选定离线回归测试通过；新增综合险完整页码映射、可选分组、真实跨页标题、每日金额与天数分离、
未知值/孤立标签/警告拦截及调用模型前阻断测试；既有 AAMI/Youi 修复测试仍通过。无付费模型调用。

后续历史段落中的“最新”均指对应轮次；以本节 `parse_check_v8` 为准。

## 第三轮：第三方险第 5 页对比表（最新）

最终结果更新为 `outputs/car_insurance/parse_check_v6/`，解析器版本 `pdfingestor.v8`。
新增 `summary_tables.py`：从横向分隔线分段的端点推导缺失的竖向列界，分别提取产品表头、保障行、状态和参考页码。
合并单元格的分类标题按整行读取，避免截断。适用入口限定为有明确 Summary of your cover 标题、Page 列以及足够规则横线的双产品概览表。

本页没有同页状态图例，不能套用 Youi 的图例匹配。
对照第 5 页原始截图确认勾叉后，新增受限的图形几何识别：圆内两条交叉对角线代表叉；特定归一化勾形轮廓代表勾。
不依据颜色猜测，也不硬编码哪项保障属于哪个产品；缺失、空心圆或未知形状输出 `UNRESOLVED` 并给出警告。
`Limited cover` 原样保留，不提升为 Covered。

结果：恢复 9 行保障、18 个产品状态及对应页码；另有一行完整的 Additional cover 分类标题。
已逐行对照原始截图，全部状态与页码一致。测试固定核对完整状态矩阵，并验证缺失图形和单独圆形不能推断为不承保。
此次仅第三方险第 5 页触发该恢复路径，5 份开发集重跑完成，138 项选定离线回归测试通过。
Youi 状态矩阵和 AAMI 侧栏修复的既有测试仍通过。未调用付费模型，未使用 holdout/test。
原图证据：`outputs/car_insurance/aami_summary_page5.png`。旧版结果保留供比较。

下方第二轮所述“第三方险第 5 页待修复”是历史状态，本轮已处理；这不等于整批 PDF 全文人工审核已完成。

## 第二轮：AAMI 标签与正文串行修复

用户复核发现 v3 中 `We cover / We don't cover` 被拆散，`cover` 混入正文。
第一轮的“保全原文”不能保证语义对应关系，v3 不应直接用于 schema discovery。

新增 `src/PDFingestor/labelled_sections.py`，使用几何分区而不是全文字符串替换：

- 根据同一左边界、垂直相邻的词识别完整标签，再寻找同高度右侧正文。
- 标签与右侧正文分别提取后组合为一个有页码、坐标、来源引擎的文本块。
- 下一标签、跨栏说明、新保障标题（字号变化）及页脚作为分区边界，避免把下一保障吞进上一限额。
- 不修改保障内容，不推断保障状态；不满足布局条件的文字维持普通路径。
- 页内普通 `We cover ...` 句子不视作侧栏。跨页继承标签尚未实现，不会擅自赋予上一页状态。

最终输出为 `outputs/car_insurance/parse_check_v5/`，v4 是边界修正前的中间结果，v1–v4 保留。
解析器版本更新为 `pdfingestor.v7`。已重跑全部 5 份开发 PDF（289 页），未使用 holdout/test。
AAMI 综合险产生 40 个标签区块，第三方险 31 个；两份输出都不再存在内容仅为 `We` 的文本块。
该统计不是全文准确率或人工逐页审核结果。第三方险第 5 页仍有对比表的 `cover` 碎片，属于另外的布局问题，继续待复核。

第 26 页示例现在为：

```text
We cover:
We cover accidental loss or damage to your car caused by an incident ...

We don't cover:
We don’t cover anything in section 3 ...

Limit:
The most we will pay for any one incident ...
```

新增合成几何测试与本地 AAMI 第 26、37 页回归，覆盖完整标签、正文对应、后续标题不误归类和普通句子不误识别。
135 项选定离线回归测试通过；Youi 24 行状态表的既有回归也通过。没有付费模型调用。

下文保留第一轮历史记录，其 v3 结果和 131 项测试数不代表当前最终版本。

## 处理方式和结果

1. **Youi 图形状态恢复**：原勾叉是 PDF 矢量路径，不是文字。新增 `vector_tables.py`，
   从同页图例获取状态含义，按归一化路径、填充/描边颜色和子图形匹配符号，再按表格单元格位置组合保障名称、产品和状态。
   不根据保险公司名称或预先写好的保障内容补答案，也不将绿色一律解释成承保。
   只有整个表格的单元格都能唯一匹配才接受；缺图例、未知符号、歧义、缺符号均放弃恢复。
   第 4 页恢复 15 行，第 5 页恢复 9 行，共 72 个产品状态；已对照保存的两页截图复核。
   `Not required` 和 `Not covered` 分别保留；跨页表格仍分为两个有独立页码的块。
2. **QBE 提示框误识别**：无数据行或只有一列的可疑表格不再遮蔽原文本，转为保留正文并发出复核警告。
   第 10 页（印刷第 9 页）`any other drivers who use your car` 和 `reduce or refuse to pay a claim` 等文本恢复。
3. **AAMI 目录顺序**：双栏检测必须有完全位于左右两侧的文本块证据，不再仅按块中心判定，避免缩进目录被误认为双栏。
   第 7 页 `Contents` 不再排在第 3、7 节标题之后。
4. **可观察性与缓存**：警告既写入 JSON，也进入两种 Markdown 输出路径；退化表格不再得到高置信度。
   解析器版本由 `pdfingestor.v5` 更新为 `pdfingestor.v6`，新缓存键不复用旧解析结果。

## 重跑与测试

- 仅对登记的 5 份 development PDF 重跑，共 289 页，未解析 NRMA holdout 或 Allianz test。
- 最终结果：`outputs/car_insurance/parse_check_v3/`。v1 原始结果和 v2 中间结果均保留。
- Youi 保留两张恢复后的状态表；QBE 综合险/第三方险保留 6/2 个表格块。
- AAMI 两份文件原来检出的 43/38 个块均未通过新的多行多列表格门槛，改为文本加警告。
  这并不是证明 AAMI 没有表格；单行或单列表格也可能是真实内容，当前保守策略会损失其结构而优先保全原文。
- AAMI 综合险、AAMI 第三方险、QBE 综合险、QBE 第三方险分别有 33、30、4、3 页带复核警告；Youi 无警告。
  这些警告表示待核实的布局，不代表对应页面都错误，也不代表无警告页面已被逐页审核。
- 131 项选定离线回归测试通过，覆盖新增解析测试、car intake、Markdown、MinerU 适配、共享 pipeline、manifest、loop、consensus、review、run。
- 新测试包含无图例、未知/缺失图形、同色不同形状、被拒表格不吞正文、警告传播以及本地开发 PDF 的可选回归。
  本地 PDF 不在 Git 中；缺文件时对应真实文档测试会 skip，人工合成测试仍运行。
- 修正了现有 Markdown 测试对 1 纳秒时间戳的假设：Windows 会将其取整，改为比较实际保存的时间戳，测试含义未变。

复现（从本 worktree）：

```powershell
& ..\..\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_vector_table_quality tests.test_car_insurance tests.test_parsed_markdown tests.test_mineru_ingestor tests.test_full_pipeline_adapter tests.test_vertical_manifest tests.test_loop tests.test_consensus tests.test_review tests.test_run
```

## 边界与下一步

本次修复的是已定位的开发集错误，不是所有 PDF 的通用表格/OCR 解决方案。
图形恢复目前只针对有同页英文图例、圆形矢量符号和可检测网格的无文字状态矩阵；扫描图片、不同形状及部分文字混合表仍需另行处理。
confidence 是启发式值，不是实测准确率。尚未生成车险 schema，也未调用付费模型。

下一步先抽查 AAMI/QBE 带警告的关键保障页，确认转为正文后没有丢失金额及对应条件，决定是否需要进一步结构恢复；
随后才启动 development schema discovery。版本适用性和 SPDS 审核仍按 intake 报告单独跟进。
