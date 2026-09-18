# Qwen 发送与连接错误重试

**1.17.6 更新：** 同时支持 Qwen 连接错误下的“网络错误”和“服务访问量较大”提示，识别当前回合的文字/图标重试按钮，并在错误卡或按钮延迟出现时继续检查。每个任务最多一次自动点击，不刷新、不重新输入；人工或站点已发起恢复请求时不重复点击。详见 [网络错误重试说明](QWEN_NETWORK_RETRY_1176.md)。

1.7.0 新增不刷新 Qwen 的会话初始化、模型保留检查和评分层关闭核验；详见 [Qwen 会话与 Agent 恢复](QWEN_SESSION_AND_AGENT_RECOVERY.md)。

历史 1.13.0 使用双栏、120 秒接收确认、600 秒生成等待和 180 秒网页异常恢复预算。当前版本的多模型候选阶段还受 `chat.timeout_seconds` 总时限约束（默认 60 秒），网页恢复不会突破这一总时限；默认全部来源成功后才融合。

**1.5.0 更新：** 针对后台标签页发送未确认，新增发送阶段的可见视口/焦点管理、串行输入占用、按钮只读检测，以及双栏和独立窗口布局。用户此次日志是 `submission_unconfirmed`，发生在投递发送动作之后；详情与复测方法见 [网页布局与发送诊断](VIEWS_AND_SEND.md)。

以下保留 1.3.1 的输入与选择器修复说明。

旧版错误“网页没有接受完整输入，或发送按钮不可用；请检查选择器”对应 `send_not_ready`。它发生在原生点击之前，表示发送前检查没有通过，并不意味着模型拒绝了问题。没有用户现场 DOM/日志，不能断言所有同类报错属于同一个原因。

## 1.13.0：红色错误卡下的圆形箭头

用户截图中，Qwen3.8-Max 已接收问题，但返回“Oops! There was an issue connecting to Qwen3.8-Max / 目前服务访问量较大，请稍后再试”的红色错误卡，下方是没有可见文字的圆形箭头。检测同时兼容站点将“目前”显示为“当前”的文案，也兼容图标按钮带精确重试 `aria-label` / `title` 的结构。1.13.0 对这个明确形态增加了当前回合恢复：

- 必须先识别出本轮新增的用户消息，且内容与本次提示一致。
- 错误必须在该用户消息之后，属于当前助手回合，并且匹配这一精确的 Qwen 中英文繁忙提示。
- 圆形箭头必须位于该错误卡内或其单回合局部容器内，在 DOM 顺序和几何位置上都紧邻错误卡。页面必须只有一个候选，并且该控件可见、启用、位于视口内、点击命中且未被遮挡。
- 页面 origin、当前会话 URL 和用户消息历史必须没有变化；正在生成、候选不唯一或无法绑定当前回合时不点击。

通过全部门控后，主进程用 CDP 执行最多一次可信点击；DOM 检查代码自身不调用 `click()`。点击回执不等于恢复成功：程序还要观察同一回合的新生成请求或生成指示，然后等待完整回答。自动重试无效时，状态会变为“请在网页重试”；此时在模型工作区打开 Qwen，手动点击这一圆形箭头，不要新建对话或重新粘贴问题。原请求会在恢复时限内继续采集。

`generation.recovery_timeout_seconds` 默认为 180，可设为 0–600。设为 `0` 只是关闭自动/人工恢复等待，不会把错误卡当成 Markdown 答案：Qwen 候选立即失败，默认 `allow_partial: false` 时整轮不进入融合。如果用户主动开启 `allow_partial`，其他成功来源可以整合，但 Qwen 错误卡仍不会被收录为原文。

## 本次修复

- **原生输入。** Qwen 先聚焦空编辑器，再通过绑定该网页的 CDP `Input.insertText` 插入完整文本一次。让浏览器编辑事件更新受控输入状态，不依赖页面 React 私有属性或手动调用事件处理函数。随后检查实际文本和按钮状态，再通过 CDP 点击一次。
- **补充 Qwen 发送控件。** 在旧配置基础上支持 `.chat-prompt-send-button button`、`button.send-button`、`.message-input-right-button-send` 等结构；不需要删除已有配置。新安装的示例配置也包含它们。相同选择器有多个可见匹配时，优先可用按钮；按钮全禁用时继续等待并明确报错。
- **识别可访问名称。** 支持 `aria-labelledby`，以及 `Send message (Enter)`、`发送（回车）` 等带快捷键的标签；仍不把任意提交按钮视作发送。
- **修正完整输入校验。** 对提示与实际输入统一 CRLF/LF 换行表示，保留内部空格与段落；真实截断仍会拒绝发送。优先识别 Qwen 专用输入框，并跟踪本次准备的编辑器，避免后出现的其他 textarea 改变检查目标。
- **保留现场与单次提交。** 未识别、禁用、遮挡或超出视口的按钮不点击。Qwen 必须识别发送控件，不在失败后改用 Enter。输入动作结果不确定时不重填，发送未确认时不重发，不把窗口切到前台。

实现接口依据：[Chrome DevTools Protocol 的 Input 域](https://chromedevtools.github.io/devtools-protocol/tot/Input/)、[Electron Debugger](https://www.electronjs.org/docs/latest/api/debugger)；目标站点为 [Qwen 官方对话页](https://chat.qwen.ai/)。页面结构会随账号、语言和 A/B 版本变化，当前选择器和语义识别只是兼容候选。

## 升级与复测

退出旧应用后解压 1.13.0 完整源码包，运行 `./run.sh`。如果在原项目目录覆盖源码且依赖已安装，可运行 `./run.sh --skip-install`。本次没有修改启动沙箱策略。

无需删除 `~/.local/share/multillm-fusion/config.json` 或登录数据。运行时会在已有选择器之后补充控件匹配；已有配置、代理和模型启用状态继续保留。自定义的优先选择器如果指向错误的可见控件，仍需要在设置里修正该选择器。

1. 确认 Qwen 已启用并登录，网页没有验证弹层。
2. 新发一个短问题，例如“回答 1+1，并给出一个两列表格”。检查 Qwen 实际发送、开始生成、原文和融合结果返回。
3. 日志应出现 `adapter.input_dispatch`（`cdp_insert_text`）、`adapter.send_state`（`ready: true`）、`adapter.dispatch`（`button`）以及 `adapter.submission_accepted`。如果出现截图中的繁忙卡，查看 `adapter.recovery_target`、`adapter.recovery_retry` 和 `adapter.recovery_observed`；只有重试成功且回答完整时才出现 `adapter.complete`。
4. 若仍失败，执行父项目 `./package.sh`，上传生成的分析包。1.4.0 起默认日志记录发送正文及回复；按钮检测不包含原始输入值。排查正文或完整按钮诊断时使用 `./package.sh --include-content`，凭据仍脱敏。

## 新诊断字段

| 原因 | 含义和检查方向 |
| --- | --- |
| `input_changed` | 实际输入与完整提示不同，或编辑器被替换；核对 `input_length` / `expected_length` |
| `send_missing` | 未识别到 Qwen 发送控件，需补充该页面的选择器 |
| `send_disabled` | 控件仍禁用，可能尚未更新输入状态、正在处理或等待验证 |
| `unrecognized_form_submit` | 表单有未识别按钮，未猜测点击或回车提交 |
| `send_obscured` | 按钮中心被弹层或其他元素遮挡 |
| `send_outside_viewport` | 滚动后控件仍不在可点击范围 |
| `page_busy` | 仍显示上一轮生成/停止状态 |
| `input_dispatch_failed` | 原生输入通道报错，可能已填入但没有执行发送；检查现场后手动重试 |

`adapter.send_state` 记录 `reason`、`ready`、`input_length`、`expected_length`、`input_source`、`send_source`、`send_candidates`。`adapter.failed` 的 `input_dispatched` 表示投递过原生输入，`dispatched` 表示投递过发送动作；两者均不等于站点已接收，接收需要单独的 `adapter.submission_accepted` 证据。

本次自动测试使用合成 DOM 和模拟 CDP，包含输入事件使按钮可用、单次发送、Qwen 繁忙卡绑定、单次重试、完整 Markdown 回传和中断清理。没有真实 Qwen 账号，也未在用户的 Electron/Linux 图形环境执行实站重试；仍需按上述步骤复测。
