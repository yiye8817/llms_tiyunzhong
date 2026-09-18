# 1.7.0：保留 Qwen 模型选择与 Agent 依赖恢复

1.8.0 保留本文的会话与依赖恢复逻辑；Agent 详细日志改为只写文件，终端显示简洁步骤和格式化结果。最新说明见 [Agent 终端与 JSON 修复](AGENT_TERMINAL_AND_JSON.md)。

请同时更新父应用和 `desktop-agent/`，重启后运行新任务。本版不恢复、重放附件里的旧动作，也没有替用户下载附件任务中提到的视频。

## Qwen 不再每轮刷新

Qwen 页面已打开且与配置同源时，直接复用已有网页。空白会话直接输入；有历史消息时，在获得可见视口和焦点后，点击网页内部的新建对话控件，等待旧消息清空、输入框就绪，再发送完整逻辑历史。

程序优先识别有明确“新建对话 / 新对话 / New chat”名称的控件，随后使用设置中的 `new_chat` 选择器。普通首页链接若尝试整页导航，会被拦截并给出错误，不以刷新作为后备方案。首次尚未加载 Qwen 时仍会打开配置网址；用户主动刷新、退出应用后重新启动或修改站点 URL/代理属于另一次页面加载，不承诺保留网站未持久化的选择。

能够读到模型选择控件时，记录操作前后的名称；例如手动选择 Qwen3.8-Max 后，连续任务不应因为程序刷新而切回默认模型。若网站的新建对话操作本身改变了可识别的模型名称，则停止发送并提示重新选择。无法可靠读取模型控件时日志明确标为 `no_reload_unverified_model`，不会编造已确认的型号。

已有草稿不会被自动丢弃。如果上一轮遗留了输入，请先处理草稿，再运行新任务。没有找到新对话控件时，可手动新建空白会话，或在设置中修正 `new_chat`，不会直接把新任务混入旧会话。

## 底部评分面板

在新建对话前后和发送前检查可识别的评分/反馈面板。只选择面板内明确的“关闭 / 跳过 / 稍后 / Close / Dismiss”等按钮，通过可信点击投递一次，再确认面板消失。不会点击星级、赞踩、评分提交，不移除网页 DOM，也不点击普通跳转链接。

评分层没有可识别关闭按钮、点击后不消失、重新出现或被其他弹层遮挡时，程序不再立即终止：若输入框仍安全可用，会先填入一次完整提示并释放页面操作权，提示用户在默认 20 秒内回到原网页手动关闭评分层并点击发送。等待期间持续监控当前网页和 Qwen 生成请求；检测到同一提示的新用户回合、生成状态、新回答或对应网络请求后，继续采集直到得到完整回答。未检测到人工发送时按时退出，不会自动重复填入或发送。点击界面“检测发送按钮”可查看 `session` 信息：新对话控件、可读取模型和评分面板候选。未取得真实评分面板 HTML，因此这些规则经过合成页面验证，仍可能需要根据现场诊断调整。

新增关键事件：

| 日志事件 | 含义 |
| --- | --- |
| `adapter.navigation_reused` | 本轮复用 Qwen 页面，未调用 loadURL |
| `adapter.qwen_session_inspected` / `adapter.qwen_session_ready` | 会话检查、旧消息清空和模型保留证据 |
| `adapter.qwen_new_chat_target` / `adapter.qwen_new_chat_dispatched` | 新对话控件定位与单次点击投递 |
| `adapter.qwen_full_navigation_blocked` | 拦截会重置页面的完整导航 |
| `adapter.qwen_rating_detected` / `adapter.qwen_rating_close_target` | 评分层和明确关闭控件 |
| `adapter.qwen_rating_closed` | 已核验评分层不可见，随后才继续输入 |
| `adapter.qwen_rating_auto_dismiss_failed` / `adapter.qwen_manual_send_required` | 自动关闭失败，转入人工发送回退 |
| `adapter.qwen_manual_send_wait_started` / `adapter.qwen_manual_send_wait` | 人工发送等待开始及剩余秒数 |
| `adapter.qwen_manual_send_detected` | 已检测到人工发送，继续监控并采集回答 |

这些步骤不改变 1.6.0 的单次发送、慢响应等待和全部来源成功后融合规则。

## 附件中的 Agent 为什么退出

`agent1.tar.gz` 中任务 `20260909T064912Z-244e5119` 在第 10 步以 `verification_pending` 停止，未达到默认 20 步限制，也没有完成搜索和下载。

实际链路是：当前 Agent Python 缺少 Playwright，`browser.open` 在页面动作前就失败；旧代码却将它标为 `outcome_unknown:true`，留下必须核验的浏览器动作。之后模型尝试不存在的 `python`、受系统包保护限制的 `pip3`，又安装到另外的虚拟环境。安装成功不代表当前 Agent 能导入该包。再次打开网页时，旧待核验状态阻止重试，最终停止。

Agent 0.4.0 修复如下：

- 工具可明确报告 `execution.status:not_started`。已确认未发生页面动作的缺依赖、无效参数等失败，不再生成虚假的待核验动作；命令无法启动也不会被说成已执行。
- 真正导航超时、点击结果未知等仍保留待核验状态，不能盲目重放。
- 增加 `environment.browser_check`：检查当前解释器、Playwright 和 Chromium 文件；缺依赖是有效诊断，不是一次失败的页面操作。
- 增加 `environment.browser_setup`：在 shell 能力授权下，用当前虚拟环境的 Python 执行固定安装命令，随后重新检查。只修复当前环境，不使用 sudo、不修改系统 Python、不另建无关环境。
- JSON 格式纠正按连续失败计算；核验纠正按待核验动作分别计算，已成功的前序动作不消耗后续动作的纠正次数。
- 失败记录保留，经过同一目标的成功重试及必要核验后才解除；失败结果文件列出具体未解决项，不只输出笼统停止提示。

## 运行方式

在父应用启用、登录并选好 Qwen 模型后：

```bash
cd desktop-agent
./run.sh run "你的原始任务" --model qwen --allow shell,browser
```

没有现成虚拟环境时，显式授予浏览器能力的 `run` 会先创建并使用项目 `.venv`；核心命令、`--help`、`doctor` 和 `tools` 不因此安装依赖。解释器优先级为 `FUSION_AGENT_PYTHON`、有效的已激活 `VIRTUAL_ENV`、项目 `.venv`、系统 `python3`。显式选择系统解释器时，安装工具会拒绝修改系统环境，并提示用隔离环境重新启动。

也可以在任务前手动准备：

```bash
./setup.sh --browser
./run.sh doctor --api
```

环境工具的安装命令每个最多运行一次，每次最多 300 秒，总命令预算 600 秒；失败保留输出和当前状态，不自动无限重装。安装成功只证明包和浏览器文件可用，不证明显示环境、系统动态库、登录或任务目标已满足。`browser.open` 后仍需实际页面观察和 `browser.verify`。

本轮日志继续逐条同步到终端、全局 `agent.log` 和 `.runtime/runs/<run_id>/events.jsonl`；所有终态写 `result.md`。生成分析包：

```bash
# 在父项目目录执行，把任务编号替换成新版终端打印的编号
./package.sh --include-content --agent-run-id <run_id>
```

默认步数与时限仍然有效。任务很长时可按需要指定 `--max-steps`；本版的修复并不是取消限制或让未完成任务报告成功。
