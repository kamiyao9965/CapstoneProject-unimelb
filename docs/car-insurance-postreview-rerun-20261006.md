# 人工复核后定向复跑（启动记录）

[中文原文 / Chinese original](#chinese-original) · [English version / 英文版](#english-version-text)

<a id="chinese-original"></a>

用户授权：2026-10-06「进行下一步」。仅开发集，使用现有 gpt-5、review_v5 schema、最新流程修复。

已准备四份新目录，后缀 `_postreview`，顺序为 Youi、AAMI Third Party、QBE Comprehensive、AAMI Comprehensive。
保留原始运行不覆盖；不重复运行已通过的 QBE TPPD；不访问验证集或测试集。
各目录的 run_plan.json 保存输入、schema 与关键运行代码哈希，执行前再次检查一致性。

每份仅启动一次，最多 3 次逻辑 generate 调用（首次 + 两次修复），全轮最多 12 次。
这不是精确 token/费用硬上限；底层现有 SDK 的网络重试策略未改。
每次本地等待上限仍是 1800 秒。遇到基础设施或未分类错误停止队列，
如有 response ID 则先只读查询原请求，不自动重提。来源：
[OpenAI Background mode](https://developers.openai.com/api/docs/guides/background)。

批次由隐藏 PowerShell 进程串行执行，启动 PID 60532（不是永久进程标识）。
本机需要保持运行和网络连接；此文不代表批次已完成或通过。
初次启动因 Windows PowerShell 对无 BOM 中文脚本的解码问题，在解析阶段退出，
未启动模型调用；修正为 ASCII 后通过语法检查并启动。

查看进度：

- `outputs/car_insurance/postreview_batch_console_v2.log`：批次启动/结束信息。
- `outputs/car_insurance/postreview_batch_stderr_v2.log`：队列中止原因。
- `outputs/car_insurance/postreview_batch_started.json`：授权、顺序及 PID。
- `outputs/car_insurance/extraction_dev_v5_20261006_<doc>_postreview/run_console.log`：单份请求进度。
- 同目录 `run_status.json`：单份结束后才产生。
- 同目录 `response_*.json`、`token_usage.jsonl`：已完成响应和已记录用量。

不要再次执行启动脚本；started 标记防止重复付费。
结束后应汇总诊断变化、用量与内容复核，不能仅以结构校验成功代替人工验收。
## 最终结果 / Final results

四份运行已于 2026-10-06 22:27 Sydney（11:27 UTC）全部结束；2 份自动通过、2 份校验失败，无超时中止。每份均调用 3 次，共 12 次、1,440,140 tokens。通过自动校验不等于人工审核通过。当前暂停继续复跑。

All four runs ended at 22:27 Sydney (11:27 UTC) on 2026-10-06: two passed automatic validation and two failed validation; none stopped on timeout. Each used three calls, totalling 12 calls and 1,440,140 tokens. Automatic validation is not human approval. Further reruns are paused.

| 文档 / Document | 结果 / Result | Tokens | 最终诊断 / Final diagnostic |
| --- | --- | ---: | --- |
| Youi | 未通过 / Failed | 454,319 | additional_item_benefits 为 null 但未列入 _unfilled / Null field absent from _unfilled |
| AAMI Third Party | 未通过 / Failed | 339,767 | Hire、Test drives、Unlicensed driving、Unregistered cars 四条例外引用不全 / Four incomplete exception citations |
| QBE Comprehensive | 自动通过 / Passed checks | 309,280 | 当前校验通过，待内容复核 / Passed current checks; content review pending |
| AAMI Comprehensive | 自动通过 / Passed checks | 336,774 | 当前校验通过，待内容复核 / Passed current checks; content review pending |

Youi 的单条最终错误不能证明没有其他内容问题；AAMI 的四条证据诊断不能直接计为四条语义遗漏。
Youi's one final error does not prove the absence of other content defects; AAMI's four evidence diagnostics are not automatically four semantic omissions.

<a id="english-version-text"></a>

## English version of the launch record

The user authorised proceeding on 2026-10-06. Development data only, retaining gpt-5, review_v5 and the latest workflow fixes.

Four new directories use the `_postreview` suffix, in this order: Youi, AAMI Third Party, QBE Comprehensive, AAMI Comprehensive. Original runs are preserved; the already-passing QBE TPPD is not rerun. Validation/test data are not accessed. Each `run_plan.json` records input, schema and key runtime hashes, checked again before execution.

Each document starts once, with at most three logical generate calls (initial plus two repairs), at most 12 for the batch. This is not an exact token or monetary cap; existing SDK network retries are unchanged. Local waiting remains capped at 1,800 seconds per request. Infrastructure or unclassified errors stop the queue; an existing response ID is retrieved read-only before any resubmission. See the linked OpenAI Background mode documentation above.

A hidden PowerShell process ran the serial queue, initially PID 60532; this is not a permanent process identifier. The launch record required the machine and network to remain available and did not claim completion. An initial Windows PowerShell decoding failure on a BOM-less Chinese script occurred during parsing, before model calls; the ASCII correction passed syntax checking and launched successfully.

Progress artifacts:

- `outputs/car_insurance/postreview_batch_console_v2.log`: batch start/end.
- `outputs/car_insurance/postreview_batch_stderr_v2.log`: queue-stop reasons.
- `outputs/car_insurance/postreview_batch_started.json`: authorisation, order and PID.
- `outputs/car_insurance/extraction_dev_v5_20261006_<doc>_postreview/run_console.log`: per-document progress.
- `run_status.json` in each directory: written when that document finishes.
- `response_*.json` and `token_usage.jsonl`: saved responses and recorded usage.

Do not rerun the launch script: started markers prevent duplicate paid runs. Summarise diagnostics, usage and content review after completion; structural success cannot replace human acceptance.
