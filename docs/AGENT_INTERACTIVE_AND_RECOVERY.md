# 1.9.0：附件分析、交互命令与父应用自动启动

配套 Agent 0.6.0。请同时更新父项目和 `desktop-agent/` 并重启父应用；仅更新 Agent 无法应用网页采集保护。

## agent2.tar.gz 的实际情况

附件包含 5 个任务的完整记录，均执行“列出 home 目录文件、大小和说明”。分析只读取了日志，没有执行其中的命令。

| 任务编号 | 记录中的事实 | 修改方案 |
| --- | --- | --- |
| `20260909T084927Z-3885025d` | `ls -lh /home/example` 成功；最终 answer 没有真实换行，含 58 处字面量 `\n`；把目录项 4K/12K 当作目录容量 | 对格式明确的 Markdown 分段恢复换行后排版、保存；原文留在会话与日志中。明确目录项尺寸与递归容量不同，未统计不能报告总占用 |
| `20260909T085250Z-b0685b4b` | `files.list('/home/example')` 越界失败，模型随后列出空工作区 `.` | 保留原目标失败，不把其他目录作为任务结果；输出 `workspace_required` 和明确恢复方式 |
| `20260909T085529Z-64900b1b` | 同样发生越界、改列空工作区、请求结束 | 交互 `/workspace` 明确切换目标目录，`/retry` 用新任务记录重试 |
| `20260909T085755Z-e53cb4af` | 同样发生越界，失败记录尚未消除 | 保留权限边界和失败状态，避免误报完成 |
| `20260909T090157Z-fc0a9028` | 连续 3 次只收到长度 18、46、30 的未闭合 JSON 片段，零工具执行 | 网页采集对明显未闭合的 JSON 继续等待；Agent 区分 `incomplete_response`，请求完整对象，不补写缺失命令 |

最后一轮能确认的是 API 交给 Agent 的内容不完整。附件没有父应用的 Electron/后端日志，无法证明当时是网页停顿、内容提取还是服务端输出导致。新版增加采集保护与 `capture` 状态，下一次可联查原因。

## 开始交互

首次请先运行父项目 `./run.sh` 安装依赖，在模型网页完成登录。以后可以从 Agent 直接启动：

```bash
cd desktop-agent
./run.sh
```

它会检查本机父应用，未启动时后台执行父项目 `./run.sh --skip-install`，等待 API 与 Electron 网页桥接都就绪。启动失败时仍保留交互入口，便于查看状态和打包日志。

在交互提示符输入：

```text
/models
/model deepseek
/workspace /home/example
列出当前目录下每个文件和目录的大小；目录容量需实际统计，无法统计的标注未知；用途推测需注明。
/logs
/pack --include-content
/quit
```

如果上一任务因工作目录不符失败，先 `/workspace /home/example`，再 `/retry`。切换目录不会自动执行任务；`/retry` 是明确重跑上一条用户任务的命令，使用新的任务编号，不回放历史工具调用。若任务先前已经产生部分结果，模型需要根据新任务中的实际观察处理已有产物。

模型切换前会查询 `/v1/models`；不存在的模型不能被选中。切换只影响当前交互会话的后续任务。每个任务独立发送用户任务与工具观察，临时能力授权不跨任务继承；启动时明确指定的 `--allow` 保留到会话结束。

## 命令与调试

| 交互命令 | 作用 |
| --- | --- |
| `/help` | 格式化帮助 |
| `/status` | 当前模型、工作目录、最近任务与父服务状态 |
| `/doctor [--api]` | 本地依赖诊断；可同时查询 API 模型列表 |
| `/tools` | 工具目录及参数 |
| `/skills [名称]` | 技能列表或正文 |
| `/skill-demo my-report` | 创建完整 demo Skill |
| `/logs` | 最近任务的结果和日志路径 |
| `/pack [--include-content]` | 本地生成代码及日志分析包，可包含本会话最近任务事件 |

非交互命令仍可单独使用：

```bash
./run.sh run "检查当前目录并生成报告" --workspace /path/to/project --model qwen
./run.sh chat --model deepseek --allow shell
./run.sh status
./run.sh doctor --api
./run.sh pack --include-content --run-id 20260909T090157Z-fc0a9028
./run.sh skill-demo my-report
./run.sh skills my-report
```

`pack` 不上传文件，默认移除日志正文；`--include-content` 保留脱敏正文。父启动输出 `agent-parent-start.log` 也纳入分析包，进程锁和账号数据不会加入。完整命令及 Skill 规则见 [Agent README](../desktop-agent/README.md)。

## 自动启动边界

默认只管理 `http://127.0.0.1:8765/v1` 或对应 `localhost` 地址；自定义本机端口需要 `FUSION_PORT` 与 `base_url` 一致。远程或其他自定义接口保持由原客户端访问，不在本机另起父服务。

启动检查只重试 GET，不重发模型 POST。健康接口正常后还须通过认证确认网页桥接；认证错误、未知服务占用端口及连接状态不明时给出具体提示。并发 Agent 使用启动锁，已有启动进程继续等待；超时不杀未知进程、不自动降低沙箱配置。

父应用保持运行，退出 Agent 不关闭父窗口。启动日志位于父项目 `logs/agent-parent-start.log`，路径遵循 `FUSION_LOG_DIR`。该捕获文件在下次启动前超过 5 MiB 时轮转，保留 3 份；运行期间继续写入。父应用自身的 `electron.log`、`backend.log`、`launcher.log` 保持原有轮转规则。

`run` 或 `chat` 的 `--no-auto-start` 禁止自动启动。独立运行 `status`、`doctor`、`tools`、`skills`、`pack`、`skill-demo` 不启动父应用。首次缺少依赖时需要运行父项目正常安装流程，自动启动不下载安装依赖。

## 输出与 JSON 修复

终端只显示必要步骤、选择提示和格式化结果；详细日志仍写全局 `agent.log` 与本轮 `events.jsonl`。正常 Markdown、JSON 诊断和 Skill 正文统一渲染。明确的双重转义 Markdown 分段会先规范化，日志事件 `result.normalized` 保留原文、转换位置和结果。

排版规范化只用于最终回答，不修改工具参数或命令字符串。已有真实换行、完整 JSON、代码围栏或无法明确判断的转义保持原义；不会对任意文本使用 `unicode_escape` 或 `eval`。

网页回答是明显的完整 JSON 对象前缀或单个 JSON 代码块、但结构仍未闭合时，内容稳定也不会提前上报完成。继续等待至完整或超时，日志带 `capture.reason`、`capture.chars` 和容器深度。完整但存在格式错误的内容仍可交给 Agent 做已有的有限修复；普通 Markdown 不要求符合动作 JSON。

若 Agent 仍收到片段，会要求模型重新输出一个完整对象，最多连续两次；仍不完整时记 `incomplete_response` 并保留现场，不猜测缺失动作。工作目录恢复和格式修复都不会把尚未执行或尚未核验的任务标为完成。

自动验证范围见 [TESTING.md](TESTING.md)。真实模型网页、登录状态与原生 Linux 图形桌面仍需在目标电脑复测。
