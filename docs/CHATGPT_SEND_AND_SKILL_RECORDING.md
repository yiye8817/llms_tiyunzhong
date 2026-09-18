# 1.11.0：ChatGPT 发送修复与 Skill 对话录制

配套 desktop-agent 0.8.0。请保留配置、登录和任务记录，同时更新父项目与子目录，然后重启父应用。

## ChatGPT 与 DeepSeek 同时打开时的发送问题

本轮没有新的实站日志，修复针对本地可复现的三条代码路径，不能据此断言用户页面的唯一故障原因：

1. 之前只有 Qwen 使用 CDP 原生输入，ChatGPT 仍用 DOM 写入。受控编辑器可能显示文字，但没有接受浏览器原生编辑事件，发送状态未更新。
2. ChatGPT ProseMirror 的段落显示间距可能被 `innerText` 表示为额外换行，导致明明输入完整仍被判定不匹配。
3. 发送控件可能与语音、停止等按钮共用标识；全页搜索还可能选中当前输入框之外的控件。

现在 ChatGPT 使用一次 CDP `Input.insertText`，先选择可见的实际编辑器，按段落、软换行读取文本并保留内部空行和缩进。发送候选限制在当前表单内，排除明确的语音、停止或取消状态。没有明确的 ChatGPT 发送按钮时返回诊断，不盲发 Enter。

发送流程继续使用多项硬门控：取得当前网页的输入租约与可见焦点 → 输入全文匹配 → 发送按钮可用且未被遮挡 → 单次可信动作投递 → 网页接收核验。投递成功不代表网页已接收；接收未确认也不会自动重复点击。ChatGPT 和 DeepSeek 的回复仍可并行生成。

本地测试覆盖 ChatGPT＋DeepSeek 在标签页、并排和独立窗口三种布局各提交一次，原 Qwen 发送、会话、评分层和慢响应测试继续通过。没有修改默认并排或 Qwen 保留模型选择的行为。

如果当前账号仍不能发送，在父界面使用“检测发送按钮”。重点查看 `adapter.send_state` 的输入是否一致、`sendTarget`、禁用/遮挡状态及 `sendActionConflict`，并区分后续 `dispatch` 与 `accepted`。界面诊断与 `package.sh` 的用法沿用 [日志说明](DIAGNOSTICS.md)。本轮没有登录真实 ChatGPT 页面或运行用户桌面的原生 Electron 验证。

## 对话录制 Skill

在 `desktop-agent/` 执行 `./run.sh`，输入：

```text
/skill create 内存检查报告
先查看系统内存和交换分区使用情况。
再检查当前工作目录中已有的内存诊断日志，分析异常增长。
保存 Markdown 报告，区分实际观测、推测和未完成的检查。
结束创建skill
```

也可用单独的“创建skill”开始，再逐条输入目标、步骤和约束。每条输入立即保存为草稿；录制期间的 `/model`、`/retry` 或形似 shell 的文字同样属于步骤，不切换模型、不执行命令。主题不能代替步骤，至少录入一个步骤后才能结束。

“结束创建skill”或 `/skill finish` 会先保存待确认草稿，再通过当前模型生成名称、描述及可选标题。模型只做命名与摘要，不修改步骤、不生成执行脚本。终端渲染完整草稿及建议命令，例如：

```text
建议命令：/inspect-memory
确认创建：/skill confirm inspect-memory
```

名称由实际录制上下文决定，上面的名称仅为示例。你可以修改并确认：

```text
/skill name memory-report
/skill preview
/skill confirm memory-report
```

确认后才会原子创建 `skills/memory-report/SKILL.md`，不覆盖已有同名目录，也不会执行其中的步骤。名称必须为最多 64 字符的小写字母、数字和分隔短横线，并避开 `help`、`model`、`skill` 等已有交互命令。

创建后无需重启，立即可用以下命令：

```text
/skill test memory-report
/memory-report
/memory-report 检查当前工作区的日志并另存报告
/skill load memory-report
```

`/memory-report` 是显式调用已存在技能的快捷方式；省略任务描述时按录制目标与步骤执行。它仍受原有工具 schema、能力授权、工作区和结果核验约束。`/skill test` 只做静态检查，不执行脚本；静态通过不代表任务已完成，提及未提供资源也不会被假定可用。

## 状态门控

| 当前状态 | 有效转换 | 其它输入 |
| --- | --- | --- |
| 普通任务 | `/skill create [描述]` 或单独“创建skill”进入录制 | 继续普通任务或既有交互命令 |
| 录制 | `/skill finish`、单独“结束创建skill”进入草稿；`/skill cancel` 取消 | 除预览和退出外，按原顺序录入步骤 |
| 待确认草稿 | `/skill name 名称` 修改；`/skill confirm [名称]` 或单独“确认创建skill”发布；取消退出创建 | 不执行，也不追加为普通任务；提示先处理当前草稿 |
| 已创建 | 返回普通任务输入，可显式 `/技能名称 [任务]` 调用 | 沿用正常任务和权限流程 |

`/skill preview` 查看当前录制或草稿；`/skill cancel`、单独“取消创建skill”取消并保留记录；`/quit`、输入处 Ctrl+C 或 Ctrl+D 退出并保留未发布草稿。控制命令必须为单独一行，多行代码或描述里提到“结束创建skill”不会跨越状态。代码首行缩进、内部空行与粘贴末尾换行均保留，已知 API 凭据会脱敏。

这些转换由精确命令、当前状态、参数校验、保存结果及用户确认共同控制，不使用单一分类器推断。模型返回动作、路径或脚本等额外字段，会被名称接口拒绝；失败或取消只回到已保存草稿，可手动命名确认，不重发失败的聊天 POST。

录制上限为 128 步、主题与步骤合计 48 KiB，生成的 `SKILL.md` 不超过 64 KiB；超限输入会提示未接受，不会静默截断。草稿位于 Agent 运行目录的 `skill-drafts/`，详细日志继续写入文件。`--v` 可显示草稿位置；本版没有跨进程恢复录制命令，保留文件用于查看与后续整理，不表示退出后自动继续录制。

Skill 编写规则见 [Agent README](../desktop-agent/README.md) 与 [编写说明](../desktop-agent/docs/SKILL_AUTHORING.md)。
