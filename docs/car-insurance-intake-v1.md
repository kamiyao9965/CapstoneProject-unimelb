# Car insurance：首批数据登记与解析检查

更新（2026-10-02）：已修复下述定位到的 Youi、QBE 和 AAMI 目录问题，开发集重跑结果在 `parse_check_v3`。
本文保留 v1 基线结论；最新结果、限制和测试见 [解析修复报告](car-insurance-parser-fixes.md)。

日期：2026-10-01。仅本地复制、哈希校验和离线解析；没有模型调用。

验证：`python -m unittest tests.test_car_insurance`，9 项通过。首次运行受系统临时目录权限阻止，获准在沙箱外重跑后通过；未修改生产解析器代码。

## 已完成

- 8 份原始 PDS 已复制到本 worktree 的 `data/car_insurance/`，原件未修改。
- 复制后逐份核对 SHA-256；8 份均不完全重复，登记工具的路径、分组和跨集合重复检查通过。
- 来源取自原文件 Windows `Zone.Identifier` 的 HostUrl；Allianz 链接移除了浏览器分析参数。
- `retrieved_at=2026-10-01` 根据原文件 UTC 最后修改时间推定，不代表独立确认过的下载日期。
- 当前适用性、补充条款完整性仍未审核；本批仅定义为这些具体版本的文档抽取样本，不代表当前完整保险合同。

## 首批划分

| 集合 | 品牌 | PDF 数量 | 用途 |
| --- | --- | ---: | --- |
| development | AAMI、QBE、Youi | 5 | 解析诊断、字段发现及后续优化 |
| holdout | NRMA | 1 | 后续检查未参与字段发现的文档 |
| test | Allianz | 2 | 留作最终测试，不参与提示词或解析器调整 |

这是小样本暂定划分，不是充分覆盖的最终 benchmark。按品牌整体分配，相关版本和补充文件必须跟随原组。
尚未完成跨品牌条款模板相似度审核。上一轮曾查看所有 PDF 的开头、结尾及 NRMA 对比页进行身份初检；
因此并非从未被人工看到的盲测集，但本轮仅解析开发集，holdout/test 未用于 schema 或模型调优。

## 离线解析结果

使用仓库 PDFIngestor 默认设置，无 vision callback，无缓存。确切版本和配置已存于解析 JSON。

| 开发集文档 | PDF 页数 | 检测为表格的块数 |
| --- | ---: | ---: |
| AAMI comprehensive | 76 | 43 |
| AAMI third party | 64 | 38 |
| QBE comprehensive | 48 | 10 |
| QBE third party | 40 | 5 |
| Youi car | 61 | 2 |

共 289 页；没有空块页面，也没有解析器 warnings。**这不等于语义解析正确；表格块数不是正确识别表格数。**

## 已确认的质量问题

1. Youi，PDF 第 4 页，`p4_t0`：原图含三险种、多行保障以及勾、叉和可选圆圈；输出仅保留三列表头，`rows=[]`。
   保障名称成为独立文本，状态和产品的对应关系丢失。该块仍被赋予 confidence=1.0，未发出 warning。
2. QBE comprehensive，PDF 第 10 页（印刷第 9 页），`p10_t0`：原图是正文加箭头提示框；输出产生
   `o us aili clai or r` 的单列表头，部分正文单词被截断。已经对照原页图像确认，不是源 PDF 本身缺字。
3. AAMI comprehensive，第 7 页：文本输出存在目录顺序异常，提示框被当作无数据行的表格；
   尚未逐页视觉审核，此项列为待复核，不计为完整文档质量结论。

本次视觉抽查了 Youi 第 4 页及 QBE 第 10 页；没有宣称逐页人工审核全部开发文档。

## 本地产物

- `outputs/car_insurance/intake.csv`：8 行登记。
- `outputs/car_insurance/provenance_v1.json`：原路径、来源依据、日期依据与待审核状态。
- `outputs/car_insurance/inventory_v1.json`：校验后清单及哈希。
- `outputs/car_insurance/parse_check_v1/`：5 份结构化 JSON、Markdown、汇总和开发集截图。
- `outputs/car_insurance/prepare_batch_v1.py`：本批次本地操作脚本；拒绝覆盖既有登记文件，不要重复运行。
- `outputs/car_insurance/render_checks_v1.py`：开发页截图脚本。

这些 PDF 和 outputs 均由已有 gitignore 排除，不会自动进入 Git 提交。

## 下一步与质量门槛

暂不开始付费 schema discovery：先在固定开发页上修复/比较解析方法，恢复保障状态和产品对应关系，
并避免装饰线/提示框吞掉正文。若选择视觉模型或其他付费服务，需要明确相应配置和运行成本。
应加入基于上述失败类型的回归检查，再复核开发集其他关键页面，记录模型/解析器版本。
不要借机检查 Allianz 测试正文来调优，也不要把手工补全的表格伪装成原解析器输出。
来源的当前适用性及相关 SPDS 仍需单独核实；若只做指定版本 PDS 抽取，应明确这一研究范围。
