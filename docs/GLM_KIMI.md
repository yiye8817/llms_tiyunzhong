# GLM / Kimi 网页模型（1.13.0）

1.13.0 新增两个内置 provider：

| Provider ID | 默认页面 | 初始状态 | 单模型 API 名称 |
|---|---|---|---|
| `glm` | [GLM / Z.ai](https://chat.z.ai/) | 关闭 | `glm` 或 `web-glm` |
| `kimi` | [Kimi](https://www.kimi.com/) | 关闭 | `kimi` 或 `web-kimi` |

两者默认不参与回答，也不会在本地 API 的 `/v1/models` 中出现。这样升级后不会未经选择就打开新网页、消耗账号额度或改变现有融合组合。

## 启用并登录

1. 启动父应用，打开 **设置 → 模型网页**。
2. 在“GLM（智谱清言）”或“Kimi”卡片中勾选 **参与回答**，然后保存。应用总共允许启用 1–5 个 provider。
3. 返回对话页，在模型工作区切换到对应页面，按站点正常流程登录；也可以在 **设置 → 浏览器登录** 导入已有的 Firefox / Chrome 等浏览器会话。导入目标只显示已保存且已启用的内置模型。
4. 回到模型网页确认账号已登录、输入框可用，再从统一对话或 API 发起任务。Cookie 导入成功不等于站点已接受该会话。

GLM 默认使用 `https://chat.z.ai/`。旧版本中恰好为 `https://chatglm.cn/` 的内置地址会自动迁移；自定义 URL、代理和选择器不会被覆盖。每个 provider 的本轮操作只信任配置 URL 的 origin。

## 旧配置升级

新安装的 `config.json` 直接包含 GLM 和 Kimi。升级时，存储层将 schema 1/2 升级为 schema 3，并仅在 provider ID 不存在时追加：

- 追加的 `glm` 和 `kimi` 均设为 `enabled: false`。
- 不改变现有 provider 的顺序、启用状态、URL、代理或选择器。
- 如果已经有自定义 `glm` 或 `kimi`，该项保持权威性，不会被新默认值覆盖。

无需删除 `~/.local/share/multillm-fusion/config.json` 或 Electron 登录目录。如果手工将配置标记为 schema 3 却又删除了这两项，自动迁移不会改写该手工结果；可在应用退出时从 `config.example.json` 复制对应 provider 块。

## 单模型 OpenAI 兼容 API

先确保父应用正在运行、目标 provider 已启用，且对应网页已登录：

```bash
export FUSION_KEY="$(cat "$HOME/.local/share/multillm-fusion/api-key.txt")"

curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $FUSION_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm","messages":[{"role":"user","content":"给出 Android Native 内存泄漏排查清单"}],"stream":false}'
```

Kimi 调用：

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $FUSION_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"kimi","messages":[{"role":"user","content":"对比 ext4 和 f2fs 的写放大特性"}],"stream":false}'
```

`glm` / `web-glm` 和 `kimi` / `web-kimi` 是等价别名。它们只返回指定网页的采集结果，不执行多模型融合。`model: "web-fusion"` 才会调用所有已启用候选；如果 GLM 或 Kimi 已启用，它们也会参与该整轮。

`GET /v1/models` 只列出已启用 provider。请求已知但未启用的 `glm` 或 `kimi` 时，返回 HTTP 404 和 `model_not_enabled`，不会在后台自动启用或登录网页。

## 回答、错误与安全边界

GLM 和 Kimi 预置了 input、send、assistant、stop 和 new-chat 的候选选择器，并使用与 ChatGPT/Qwen 同类的 CDP 原生输入通道。发送后仍必须观察到新用户回合、生成状态或关联网络流，才认定网页已接受请求。

已识别的本轮错误卡不会被当成助手 Markdown。GLM/Kimi 只对配置的重试选择器或具有明确“重试/重新生成”可访问名称的当前回合控件执行单次可信重试；不会仅凭一个无标签 SVG 图标猜测。自动重试无效时，界面提示在当前网页手动处理，并在有限恢复窗口内继续采集同一回合。

每轮任务从开始到采集结束都绑定 provider URL 的 origin。任务期间如果因 OAuth、验证或手动导航跳到其他 origin，程序会返回 `provider_origin_changed` 并停止本轮输入、点击和采集。请在应用空闲时完成跨站登录，然后返回配置的 GLM/Kimi 模型页再发起任务。

## GLM 保留网页模型选择

1.17.1 起，GLM 在配置同源页面已打开时不再每轮刷新。先在网页中手动选择 `glm-5.3-flash`，后续任务复用页面；旧消息通过网页内新建对话清空，模型发生可识别变化时停止发送。API provider 仍使用 `glm` / `web-glm`。详见 [GLM 不刷新与本次验证范围](GLM_NO_REFRESH.md)。

## 验证范围

已用合成 GLM/Kimi DOM、模拟 CDP、配置迁移和本地 API 测试检查 provider 加载、输入/发送、回答转 Markdown、本轮错误与重试控件绑定。开发环境没有真实 GLM 或 Kimi 账号，也未在用户的 Electron/Linux 桌面完成实站登录、发送、恢复与采集验证。站点 DOM、风控、验证码和账号权益可能导致现场差异；启用后请先用短问题逐个复测，失败时使用 `./package.sh --include-content` 生成脱敏诊断包。
