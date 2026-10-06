# 人工复核后的归档与离线修复

[中文原文 / Chinese original](#chinese-original) · [English version / 英文版](#english-version-text)

<a id="chinese-original"></a>

## 审核归档

用户已确认“人工审核通过”“都核实了”。审核来源、范围及历史文件 SHA-256 保存在
`docs/car-insurance-human-review-20261006.json`。

通过的 v4 AAMI Comprehensive 已整理为可直接阅读的 JSON：
`outputs/car_insurance/human_review_20261006/aami_comprehensive_v4_reviewed.json`。
已验证它与 lookup_1.json 中 output_text 解析后的对象完全相等，只改变排版。
没有覆盖原始响应、历史运行状态或 schema，也没有将 v4 结果当作 v5 成功。

其余清单项记录为已复核但仍待处理。用户没有提供 QBE 每条诊断的单独裁决，
因此不擅自将五条证据诊断全部标为语义遗漏或全部豁免。

## 通用流程修改

文件：`src/car_insurance/extraction_diagnostics.py`。

- 增补抽取/修复约定：合并规则仍须覆盖每个引用块；独立条件必须结构化，不能只存在于父规则证据。
- 明确事先授权、另有规定等例外必须保留；引用包含完整引导句及适用列表，保持原页码。
- 证据诊断先检查已表达的含义，保留正确例外，仅修复引用；不得删来源 ID 或清空保障来过检查。
- 延续上一轮具名档位的拖车责任及共同额度检查，不手填历史输出。
- 修复来源诊断的路径广播：同一来源被多条记录引用时，只向实际失败的记录报错。
  每条记录仍使用完整清单检查父例外关系，全局缺失映射仍在文档级校验。

文件：`tests/test_car_extraction_diagnostics.py`。
新增多产品相同来源和共享规则相同来源的两个反例测试，检查错误路径与不修改候选的约束。

选定离线回归 **219 项全部通过**（68.724 秒），范围与上一轮 217 项一致并新增上述两项。
首次沙箱运行因 Windows 临时目录权限失败；获准在沙箱外重跑后全部通过，模型调用为模拟。

## 已保存结果回放

新报告：`outputs/car_insurance/post_review_offline_20261006/audit_report.json`。
本轮离线回放全部 13 个 v5 已存响应及 3 个找回响应；没有新请求。

最新结果保持：QBE TPPD 0 条；Youi 3 条；QBE Comprehensive 5 条；
AAMI Third Party 2 条；v5 AAMI Comprehensive 找回第二轮 4 条；v4 AAMI 找回结果 0 条。
这说明没有通过放松校验消除历史内容问题，不证明修改后的提示要求已改善模型生成。

回放工具的 `checks_passed_not_human_approved` 仅表示工具不做人工审核判定；
v4 AAMI 的用户审核结论以独立审核记录为准。

## 下一步边界

本轮未调用付费生成、未访问验证集/测试集、未 commit/push。
下一轮应在新目录受控复跑仍有问题的开发集文档，保存代码/提示/schema 版本，
设置请求次数及用量限制；超时先查原请求，不重复提交。实际生成验证前不宣称遗漏已修复。
开发集验收后才能冻结版本并进入验证集。
<a id="english-version-text"></a>

## English version

### Review archive
The user confirmed that the human review passed and all listed findings were checked. Review scope, provenance and historical SHA-256 hashes are recorded in `docs/car-insurance-human-review-20261006.json`.

The accepted v4 AAMI Comprehensive result is available as readable JSON at `outputs/car_insurance/human_review_20261006/aami_comprehensive_v4_reviewed.json`. Its parsed object is identical to `lookup_1.json.output_text`; only formatting changed. Original responses, run status and schema remain untouched. Acceptance of v4 does not imply a successful v5 result.

Other findings are reviewed but still open. The user did not supply individual adjudications for the five QBE evidence diagnostics, so they are neither all labelled semantic omissions nor all waived.

### General workflow changes
In `src/car_insurance/extraction_diagnostics.py`:

- Merged rules must retain every cited source block. Independent qualifications must be structured, not merely quoted in parent evidence.
- Prior-authorisation and unless-otherwise-stated exceptions must be retained, with complete introductory sentences, applicable lists and original page numbers.
- Evidence repairs must preserve correctly expressed exceptions. Deleting source IDs or emptying coverage to pass validation is prohibited.
- Named-tier trailer-liability and shared-limit checks remain active; historical outputs are not hand-filled.
- A source cited by multiple records now produces diagnostics only on the failing record. Each record retains full-checklist parent-exception checks; missing mappings remain a document-level check.

Two regressions in `tests/test_car_extraction_diagnostics.py` cover identical source IDs across products and shared rules, precise paths and candidate immutability.

All **219 selected offline tests passed** in 68.724 seconds: the previous 217 plus these two. The first sandbox run hit Windows temporary-directory permissions; the authorised rerun outside the sandbox passed, using mocked model calls.

### Replay
`outputs/car_insurance/post_review_offline_20261006/audit_report.json` replays all 13 saved v5 responses and three recovered responses without new requests.

Latest diagnostic counts remain: QBE TPPD 0; Youi 3; QBE Comprehensive 5; AAMI Third Party 2; recovered v5 AAMI Comprehensive response 2 has 4; recovered v4 AAMI has 0. Checks were not relaxed to erase historical defects. This does not establish that the new instructions improve actual generation.

The replay label `checks_passed_not_human_approved` means that the tool does not adjudicate human approval. The independent user-review record governs v4 AAMI acceptance.

### Boundaries at this stage
No paid generation, validation/test-set access, commit or push occurred during this implementation stage. A subsequent controlled run must use fresh directories and record code, prompt and schema versions, request/usage limits, and retrieve timed-out requests before resubmission. Do not claim generation defects resolved before testing actual outputs. Freeze the version and move to validation only after development acceptance.
