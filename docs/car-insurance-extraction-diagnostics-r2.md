# v4 抽取流程改进 r2（2026-10-05）

状态：已完成离线实现及旧响应回放，**没有新增模型调用，没有把失败响应改成成功数据**。
本轮改的是 v4 的运行时提示、诊断和修复流程，不改变候选 schema 的字段结构，也不覆盖历史 schema/请求/响应。
旧的 [v4 真实试跑失败](car-insurance-extraction-sample-v4.md) 结论仍然成立；真实模型能否稳定修好，要由下一次受控试跑验证。

## 五项处理

| 项目 | 已实施内容 |
| --- | --- |
| 表格约定 | columns 对应所有 cells；label 是额外行标识，不替代一个 cell。提示中给出两列示例；报错精确到表与行的 cells 路径，包含预期/实际列数。不会自动插入或删除模型单元格。 |
| 修复上下文 | 仅 v4 自动启用：带入上一轮完整、紧凑 JSON，以及去重后的累计错误历史；不层层嵌套所有旧响应。仍最多两次修复，所有检查重新执行，旧错复发仍拒绝成功。 |
| 合并诊断 | JSON 结构通过之后，同时检查表格、例外、日租费、excess、来源映射和选定共享关系。例外报错包含规则 ID、字段路径及 source_clause_ids。保留其他基础业务失败；并非所有旧业务检查都已改成全量收集。 |
| 证据归属 | 完整证据可以放在所属规则的 evidence 或该规则的 exceptions.evidence。允许同页、原顺序的相邻引用完整拼接；不允许删字、改页、重排、模糊匹配。仅构造验证视图，绝不改写候选。 |
| 共享额度回归 | 对明确的单产品来源关系检查责任共同池，以及同一附加选项中 fire/theft/attempted theft 的共同赔付池；缺事件、缺池成员、缺反向引用会报错。责任池保留 per_incident/aggregate 及可明确读取的源金额。 |

证据诊断现在区分：

- `source_mapping_missing`：没有可验证的归属映射，需要检查是否漏抽；不直接认定语义遗漏。
- `evidence_mismatch`：已有映射，但完整原文/页码不匹配；不再同时重复报为“缺少该来源”。
- 例外仍需完整的相关来源块。只有 “unless ...” 短语、却没有完整条件范围，依然会被拦住。

这不会自动修好 PDF 侧栏串入正文的问题。模型可以在摘要中解释原文，但证据字段仍必须忠于输入；S4 的重排引用仍需处理，不能用模糊匹配放行。

## 修复上下文预算

- 默认关闭，只有 `car_insurance.review_v4` 的真实抽取路径开启；旧 v2/v3、health/travel 不自动带入新上下文。
- 最新候选最多 **240,000 字符**；超过时整份省略并明确说明，不截取半个 JSON 冒充完整结果。
- 当前错误与累计历史分别最多 **32,000 字符**，完整错误仍保留在运行记录中。
- 字符数是确定性上限，不是精确 token 估计；候选加入上下文会增加下一次调用的输入成本。
- 三个历史候选紧凑化后分别为 53,482、53,482、55,710 字符，均可完整带入预算。
- 新上下文只能帮助模型保留已修正字段，不能保证它必然做到；回归用失败序列验证“旧错误回来时仍然拒绝成功”。

## 零调用回放结果

最终报告：`outputs/car_insurance/extraction_diagnostics_r2_20261005_verified/replay_report.json`。
保留旧文件 SHA-256，记录本次运行代码哈希，未重新解析其他 PDF，未使用 holdout/test。

| 保存的输入 | 新检查结果 |
| --- | --- |
| v3 成功记录，只检查新共享关系 | 0 条共享关系问题；原来的两个正确池未被误报 |
| v4 response_1 | 仍失败；12 条诊断，其中 2 条共享关系问题 |
| v4 response_2 | 仍失败；52 条诊断，其中 2 条共享关系问题；包含多处非法来源 ID |
| v4 response_3 | 仍失败；13 条诊断，其中 2 条共享关系问题 |

这些是诊断消息数，不是独立错误个数、准确率或人工标注分数。不同轮次不能仅按消息数排序质量。
v3 只验证共享关系，不将它伪装为符合 v4 的记录。v4 的原始失败候选不会输出到成功产品文件。

复现回放（输出目录必须不存在，不需要 API key）：

```powershell
..\..\.venv\Scripts\python.exe -X utf8 -m src.car_insurance.replay_diagnostics --trial-dir outputs/car_insurance/extraction_sample_v4_20261005_qbe_tppd --baseline outputs/car_insurance/extraction_sample_v3_20261003_qbe_tppd/qbe_tppd_1123.json --output-dir outputs/car_insurance/my_diagnostics_replay
```

## 离线测试

最终选定 **178 项测试通过**，覆盖新诊断/修复上下文、car v2/v3/v4、抽取器、contract、schema 校验、canonical 和 travel migration。
其中新增的专项用例检查精确报错、合并诊断、证据嵌套/页码/缺词/顺序、共享池成员与源金额、三个事件的共同赔付，以及旧错误复发时拒绝成功。

```powershell
..\..\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_car_extraction_diagnostics tests.test_structured_output tests.test_car_schema_revision_v4 tests.test_car_schema_revision_v3 tests.test_car_schema_revision tests.test_car_insurance tests.test_extractor tests.test_extraction_contract tests.test_schema_validation tests.test_canonical_schema tests.test_travel_schema_migration
```

另一次较大范围尝试中的旧 `test_json_artifacts.test_writer_does_not_follow_predictable_temporary_symlink` 因 Windows 缺少符号链接权限报 WinError 1314；没有把它计为通过，也未修改该测试绕过权限。
首次沙箱运行还遇到现有临时文件测试的目录权限限制，使用获准的临时目录权限重跑后通过。以上不代表仓库所有测试已通过。

## 边界与后续

- 共享关系检测是保守的文本模式，**当前只对单产品文档执行**。不硬编码保险公司、页码或金额，但也不是任意保险措辞的通用语义证明。
- 模式不命中、或是多档位文档时，不能据此判定共享关系完整。原文中复杂分支、例外范围及无保险司机上限仍需要复核。
- JSON 结构未通过时不运行依赖字段形状的深层检查，避免诊断器崩溃；结构通过后才合并安全可执行的诊断。
- 本轮没有修改历史 schema 快照或将其标成人工批准；未来试跑需要记录新的运行代码指纹，不能复用旧试跑目录/started 标记。
- 下一步是用同一 QBE 开发样本做一次独立受控模型试跑，保留 v3/v4 历史，检查内容改善和共享池是否同时保持。本轮未自动启动该付费步骤。
- 未 commit/push 本轮改动。
