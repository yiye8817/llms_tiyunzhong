# 网页布局与 Qwen 发送诊断（1.6.0）

1.7.0 新增不刷新 Qwen 的会话初始化、模型保留检查和评分层关闭核验；详见 [Qwen 会话与 Agent 恢复](QWEN_SESSION_AND_AGENT_RECOVERY.md)。

## 这次日志说明了什么

用户提供的 backend.log 中，2026-09-09 02:10:56 UTC，Qwen 进入“已投递发送动作”的 generating 阶段；约 15 秒后返回 submission_unconfirmed。DeepSeek 正常返回，最终结果也明确标注未纳入 Qwen。这个错误表示程序没有观察到网页接收请求的证据；generating 状态本身不能证明网页已开始生成。

日志没有 Electron 侧的实际按钮和焦点记录，不能据此断定 Qwen 3.8 换成了哪个 CSS 选择器。代码中的后台标签页使用 setVisible(false)，此前即使设置了 backgroundThrottling:false，仍会向隐藏的网页直接投递输入。这与“哪个标签在前面，哪个才能发送”的现场表现吻合。

1.6.0 在填充和完整发送手势期间，依次让目标网页处于可见视口，聚焦对应 WebContents 并处理 CDP 页面焦点。各模型不能同时争抢这段输入过程；一旦收到完整手势的 CDP 回执，就恢复用户布局并释放占用，接收确认与回答采集继续并行。接收确认默认延长到 120 秒，总生成默认 600 秒；所有来源成功完成后才融合，详见 [慢响应与 JSON 修复](WAIT_AND_PROTOCOL.md)。发送失败或取消时同样释放占用，不盲目补发点击或回车。

实现使用 [Electron View 的子视图管理](https://www.electronjs.org/docs/latest/api/view)、[BaseWindow 的视图生命周期](https://www.electronjs.org/docs/latest/api/base-window) 和 [CDP 页面焦点模拟](https://chromedevtools.github.io/devtools-protocol/tot/Emulation/#method-setFocusEmulationEnabled)。焦点模拟不可用时仍检查页面实际状态，不把命令调用成功当作输入成功。

## 三种显示方式

首次启动和首次升级默认双栏并排（只有一个启用模型时使用标签页），之后保留用户主动选择。网页区域上方可选择显示方式：

| 方式 | 使用方法 | 大小调整 |
| --- | --- | --- |
| 标签页 | 点击模型标签查看网页；发送时程序临时显示目标网页，完整发送手势回执后恢复布局 | 拖动对话区与网页区之间的分隔条 |
| 双栏并排 | 两个网页同时显示；通过各栏模型选择框选择不同的已启用模型 | 拖动左右网页之间的分隔条；外层分隔条调整网页区总宽度 |
| 独立窗口 | 每个已启用模型使用自己的窗口；切换方式沿用同一网页和登录会话 | 按 Linux 窗口管理器的方式拖动窗口边缘，分别调整宽度和高度 |

关闭独立窗口会隐藏该窗口，保留网页和登录会话。若 API 请求正在等待已关闭或最小化的模型窗口，可点击“重新显示窗口”继续；这个入口在等待任务时仍可使用，不会重发已有请求。

主窗口高度随整个窗口缩放。分隔条支持键盘左右方向键，界面保留最小宽度，避免出现不可操作的零尺寸网页。显示方式和分隔比例会保存在本地。调整布局不会新建模型对话或重新发送请求；窗口/网页之间沿用已有 WebContents 与持久会话。

任务发送期间暂时锁定显示方式和模型切换，避免输入过程中替换目标。打开设置、按钮诊断结果或进行拖动时，原生网页会按需要隐藏，防止遮住本地控件。自动发送不会调用窗口 focus 或 Page.bringToFront 抢占操作系统前台；用户主动点击模型标签定位独立窗口时会激活该窗口；原生焦点行为仍需在目标 Linux 窗口管理器下复测。

## 检测 Qwen 当前发送按钮

1. 启用并登录 Qwen，在网页内选择所需模型（例如 Qwen 3.8），确认页面已加载。模型版本名称本身不是发送按钮选择器。
2. 选中 Qwen，点击“检测发送按钮”。此操作只读取现有网页，不填写、点击、滚动或发送内容。
3. 查看诊断结果，可复制 JSON。检测会在隐藏网页以显示结果之前完成，避免人为把 visibilityState 变成 hidden。
4. 若当前草稿为空，发送按钮显示 disabled 可能是正常状态；可以手动在网页输入短草稿后再次检测，再清除草稿进行程序发送复测。不要把空草稿下的 disabled 直接当成选择器错误。

点击工具栏检测时，焦点可能已经移到本地工具栏，手动报告中的 hasFocus:false 不一定代表发送故障；应结合自动发送获得视口与焦点后的日志判断。

诊断包含：

- 输入框的 selector、命中来源、字符数和焦点状态；不把原始输入值放入按钮检测结果。
- 发送按钮的 selector、标签、可访问名称、禁用状态、矩形位置。
- 按钮是否在视口内、中心点实际命中哪个元素、是否被遮挡。
- 各候选选择器命中数量，以及页面 visibilityState / hasFocus。

已有选择器仍有优先级。当前兼容候选包括 #send-message-button、.chat-prompt-send-button button、button.send-button、.message-input-right-button-send，以及具有“发送/Send message”等可访问名称的控件。程序不会只凭箭头 SVG 的形状点击未知按钮。若自定义选择器指向了错误控件，应根据实际诊断在设置中修正。

## 日志与现场复测

自动发送会记录完整输入校验、控件定位、页面焦点、CDP 回执和接收证据；手动检测写入 adapter.send_diagnostic。重点区别：

| 记录/错误 | 表示什么 |
| --- | --- |
| adapter.dispatch | 已准备投递发送动作；还不能证明网页接收 |
| adapter.submission_accepted | 观察到生成状态、新回复或匹配的新用户回合 |
| submission_unconfirmed | 投递后仍没有接收证据，不会自动重发 |
| send_missing / send_disabled | 没找到可识别控件，或控件仍禁用，尚未点击 |
| send_obscured / send_outside_viewport | 命中点被遮挡，或目标不在可点击视口 |
| page_not_interactive / input_focus_failed | 可见性或输入焦点没有通过检查，未执行发送 |
| input_lease_timeout / input_view_hidden | 等待可操作窗口超时，或输入过程中窗口被隐藏；检查设置页、关闭和最小化状态 |

在标签页模式选择 DeepSeek，让 Qwen 留在后台，再发送一个短问题；交换前台模型后重复检查。随后切到双栏和独立窗口，确认两个模型各发送一次并都返回原文。窗口切换本身不应清空网页、丢失登录或重复提交。

复现后在父项目目录生成分析包：

```bash
./package.sh --include-content
```

分析包包含 logs/electron.log、logs/backend.log 和相关轮转日志。只上传 backend.log 无法完整看到按钮匹配和原生视图状态。需要核对已保存答案时可另加 --include-markdown。日志正文包含任务与模型回答，已知密钥和 Cookie 等凭据会脱敏。

## 本地原生检查

随包提供只访问 localhost 合成页面的检查：

```bash
npm run smoke:layout
```

它用于目标 Linux 桌面上的原生视图、焦点和单次发送检查，不访问真实模型账号。当前交付环境没有可用 X11 显示，Electron 在渲染器启动前因环境权限问题以 SIGSEGV 退出，因此原生检查未通过；自动回归使用模拟 Electron/CDP 和 DOM。实际 Qwen 3.8 页面与登录后行为仍需本机复测。
