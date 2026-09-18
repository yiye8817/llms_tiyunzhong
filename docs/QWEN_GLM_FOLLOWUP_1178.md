# Qwen / GLM 发送链后续修复：1.17.8

基线：`multillm-fusion-1.17.7-qwen-agent-glm-send.tar.gz`。父应用升级为 1.17.8；Agent 仍为 0.16.1，本轮没有修改 Agent 源码、JSON 修复算法或其工具权限。

## 1. Qwen 重试后的异步提交

1.17.7 首次发送有短暂的页面保持窗口，但自动点击重试后立即释放输入页面；初次失败请求还留下 `observed=true`。本版将初次发送与重试统一使用 `settleSubmission`，每次点击前保存请求计数检查点，只接受本次点击后新发起的生成请求，或新的有效生成状态，不把旧请求的 observed 标志当作重试已经提交。

继续使用已有 `submit_settle_seconds`，默认 2 秒、上限 10 秒；观察到本次提交则提前释放。失败、取消和超时都释放输入占用。每轮仍最多自动重试一次，不重填文本、不创建另一条问题，不把一次点击的确认等同于已经生成完整答案。

## 2. Qwen 长输入把重试按钮推到屏外

本地 Chromium 集成测试中，完整 Agent 提示渲染为用户消息后，当前错误卡的 Retry 按钮可能落在可视区域外。以前仅检查 `retryAvailable`，始终无法进入重试。

新增只读标记 `retryRevealable` 与显式 `recoveryReveal` 动作。仅当已确认当前用户回合、唯一错误卡、唯一重试按钮、同一会话和没有新的生成请求时，运行器获取输入占用，将该按钮滚动进视区。随后重新读取页面状态、确认同一 DOM 节点、会话和用户文本，并做遮挡/禁用/实际命中检查，才允许原有一次重试点击。

每个任务最多进行一次该滚动尝试。普通 inspect 不滚动；历史回合、其他用户输入、模态弹层、歧义按钮、已使用的重试不会授权滚动或点击。滚动不能代替验证码验证，也没有添加验证码绕过。

## 3. 网络失败归因

旧观察器主要按 POST 路径匹配，把 `/chat`、`/chats/<id>/messages` 一并视为生成接口，且未检查请求来源及最新用户消息。因此旁路保存、标题生成或其他域名请求的失败也可能终止当前回答。

本版增加有界 `requestPromptEvidence` / `classifyGenerationRequest`：

- 已知生成端点、同源且没有可用正文：仍跟踪，避免长输入的 CDP postData 缺失时掩盖真实故障。
- 正文有明确消息结构：只比对最后一条消息，不搜索旧历史中的任意相同字符串。最新用户不同或以 assistant 结尾的保存正文仅作诊断。
- 跨源生成端点：需要本轮最新用户文本精确匹配；未知同源端点需要该匹配及 `stream: true`。未知结构继续依靠原有 DOM 采集，不伪造网络成功。
- 同一次已跟踪请求的重定向继续归属原尝试，不重复计数。
- 已确认本轮请求的 HTTP 429/5xx、loadingFailed 与流中断仍然保留，不会因为旁路请求成功而消除。

新增日志字段示例：

```json
{"event":"adapter.network_request","tracked_response":true,"attribution":"known_endpoint_current_prompt","prompt_relation":"exact"}
```

`prompt_relation` 表示“最新消息匹配”；旧 `prompt_match` 仍是请求体内是否出现完整原文的诊断，两者含义不同。新增分类字段不包含正文、Cookie、认证头或 query token；原有完整提示/响应日志的保存策略没有改变，因此整份运行日志仍须按私人数据处理。

## 4. GLM 输入框先于发送按钮存在

对于未知英文输入框，旧后备发现依赖可识别发送按钮；部分编辑器只在输入文字后挂载按钮，造成循环依赖。本版补充明确的聊天英文 placeholder/data-placeholder、`data-testid="chat-input"` 包装层内编辑器的识别。输入框可先获得焦点和原生文字，再等发送按钮出现，并执行完整文本及按钮命中核验。

同时过滤只读区域、侧栏搜索、反馈输入、对话框、历史回答/用户消息中的示例编辑器；同一选择器发现多个独立匹配输入框时不猜测。过滤使用控件标签，不能把用户正文里的 web.search、搜索或反馈字样当作控件身份。

不盲按 Enter，不使用私有 React/Vue 回调，不修改站点登录、代理或模型选项。已有 GLM 选择模型后不主动刷新逻辑保持。

## 5. 升级与时限

关闭旧父应用及 Agent，再从新目录启动：

```bash
mkdir -p fusion-1178
tar -xzf multillm-fusion-1.17.8-qwen-glm-followup.tar.gz -C fusion-1178
cd fusion-1178/multillm-fusion
node -p 'require("./package.json").version'
./run.sh
```

版本应为 1.17.8。仅运行新版 Agent、继续连接旧父应用不生效。登录数据无需删除。复制自己的 `desktop-agent/agent.config.json` 时要检查绝对路径；本包没有个人配置或登录数据。

配置字段未改变：双模型候选默认共享 60 秒时限，输入、重试、页面保持都计入总时限；不会为本次修改额外延长。网页异常恢复等待和发送后页面保持的原有设置继续可用。

## 6. 适用范围与未确认事项

本版修复的是代码检查及可重复本地测试发现的发送/重试/归因问题。没有新的现场网络日志，也没有真实 Qwen/GLM 登录账号端到端测试；不能据此断定用户线上所有网络错误的根因已解决。不会通过删减 Agent 完整上下文、换模型、关闭安全校验或伪造结果来掩盖失败。

诊断可对照同一 request_id/job_id 的 `adapter.network_request`、`adapter.request_payload`、`adapter.network_response`、`adapter.network_failed`、`adapter.recovery_target_revealed`、`adapter.submit_settle_finished` 和 `adapter.failed`。其中 settle 的 operation 区分 `initial_send` 与 `retry_current_turn`。

相关协议参考：Chromium 官方 CDP Network、Emulation 域文档：
https://chromedevtools.github.io/devtools-protocol/tot/Network/
https://chromedevtools.github.io/devtools-protocol/tot/Emulation/
