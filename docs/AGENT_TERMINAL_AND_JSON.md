# 1.8.0：Agent 简洁终端与 JSON 修复

1.9.0 保留本版终端与文件日志规则，新增交互命令、父服务自动启动及不完整 JSON 采集保护；最新用法见 [交互与恢复说明](AGENT_INTERACTIVE_AND_RECOVERY.md)。

配套 Agent 0.5.0。更新父项目和 `desktop-agent/` 后重启父应用，Agent 的运行命令与配置继续有效，无需增加关闭终端日志的参数。

## 终端显示

默认显示模型等待、当前工具步骤及摘要、工具结果、必要的格式修正或依赖准备进度、能力选择提示和最终结果。不会打印请求正文、模型动作 JSON、分段载荷或整段命令输出。尚待核验的网页或桌面动作显示“操作已触发，等待核验”，任务未完成时会明确显示失败或停止。

授权提示保留完整命令、路径和 URL；长文件正文仅显示明确标注的预览。原有 `--allow` 和交互授权规则继续有效。

最终回答会在终端排版：Markdown 标题、段落、列表、引用、表格与代码块分开显示；完整 JSON 结果缩进显示。代码内容保持原样，不因排版执行其中的命令。终端宽度用于中文文本换行；代码和过宽单元格允许保留原行。重定向输出、`NO_COLOR` 或 `TERM=dumb` 环境下不输出 ANSI 样式。`tools` 和 `doctor` 命令仍输出可供程序解析的 JSON。

## 文件记录

| 文件 | 内容 |
| --- | --- |
| `.runtime/logs/agent.log` | 全局详细 JSONL 日志，5 MiB 轮转，保留 3 份 |
| `.runtime/runs/<run_id>/events.jsonl` | 本轮同样的详细日志，逐条立即写入，不轮转 |
| `.runtime/runs/<run_id>/result.md` | 任意终态的状态、步数和结果或错误 |
| `.runtime/runs/<run_id>/final.md` | 成功任务的 Markdown 回答 |
| `.runtime/runs/<run_id>/transcript.json` | 本地完整任务会话 |

启动时显示工作目录与任务记录目录，结束时列出结果和可用日志的准确路径；日志文件不可写时发出简短告警，并继续尝试保存另一份日志。详细日志默认保留脱敏后的请求、回复、工具参数和结果。`--log-metadata-only`、`FUSION_LOG_CONTENT=0` 或 `log_content:false` 只关闭文件日志正文，不改变终端简洁模式，也不关闭 transcript。

## JSON 处理顺序

1. 接受单个 JSON 对象，或完整包在一个 JSON 代码块中的对象。
2. 对确定性的格式污染进行归一化：首部 BOM、字符串外数组括号的 Markdown 反斜杠、完整合法值后的尾逗号。字符串只有一个例外：固定 `files.*` 文件动作及 `file.*` 固定别名的顶层 `arguments.path` 可在严格结构探测后把非法 `\_` 恢复为 `_`。工具名、命令、网址、摘要、计划、文件内容和其他非法标点转义不作替换。
3. 仍无法解析时，记录原文与错误位置，先调用开源 `json_repair`；它仍无法生成通过协议校验的对象时，再发送一次隔离的大模型 JSON 修复请求。两级兜底的开始、成功、失败都会写入事件日志；不会自动重发原任务。
4. 修复结果仍须通过单对象、重复键、有限数字、动作结构、已知工具、参数及能力授权检查，之后才允许工具执行。不会从带说明的文章中截取动作，不通过 `eval` 执行内容，也不重放已经发出的操作。

兼容旧网页转换时，`browser.open` 的 URL 仍只解包显示地址与目标一致的完整网址链接。JSON 语法修复不等于 HTTP 重试；502 或请求超时不会自动重发 POST。

| 日志事件 | 作用 |
| --- | --- |
| `model.raw_reply` | 保存模型原始回复 |
| `model.protocol_normalized` | 确定性归一化方法、原文与动作 |
| `model.invalid_protocol` | 解析错误与失败回复 |
| `model.json_repair_started` | 记录本地、开源 `json_repair` 或大模型修复阶段开始 |
| `model.json_repair_succeeded` | 记录通过本地协议校验的修复候选及其方法 |
| `model.json_repair_failed` | 记录该阶段失败原因并进入下一级兜底 |
| `model.local_protocol_repair_failed` | 本地 Python 修复器拒绝不确定变换；`server_retry:false` |
| `model.repair_requested` | 仅用于可重新规划的 schema、工具选择等语义错误 |

## 本次附件

新附件 `agent1.log` 的回答含字面量 `\\n`，并在 Markdown 项目符号与强调位置出现 JSON 非法的 `\*`，最终报第 182 列 `Invalid \\escape`。0.12.0 在本地恢复经结构证明的正文 Markdown 转义；原始 payload 不变并保留审计。无法证明属于回答正文的损坏不会修复，也不会再次请求服务器改写。

同一记录还包含当前 Python 缺 Playwright、安装到另一个环境，以及执行前失败被错误保留为待核验等问题。1.7.0 的依赖恢复修复继续保留：检查正在运行的解释器，在其虚拟环境中安装和核验；明确未开始的操作不阻塞后续修复。实际已发出但结果未知的动作仍必须核验，不能直接宣布任务完成。细节见 [依赖恢复说明](QWEN_SESSION_AND_AGENT_RECOVERY.md)。

需要复查新版任务时，在父目录执行：

```bash
./package.sh --include-content --agent-run-id <新版任务编号>
```

该命令生成本地分析包，不自动上传。真实网站及原生 Linux 桌面仍需在目标电脑复测，自动测试范围见 [验证记录](TESTING.md)。
