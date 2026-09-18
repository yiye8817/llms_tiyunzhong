# 1.17.8 实测记录

执行日期：2026-09-12。Python 3.13.5，Node 22.16.0，Chromium 144.0.7559.96。基线为完整 1.17.7；所有下列通过数来自本轮实际执行，不沿用历史数字。

## 结果

| 检查 | 结果 |
|---|---:|
| 父应用 Python 全集 | 130/130 |
| 选定 Node 回归（包括新增 14 项网络归因与 4 项重试等待测试） | 191/191 |
| 新增 GLM 输入框 Chromium 组 | 13/13 |
| 1.17.7 长输入/GLM Chromium DOM 回归 | 15/15 |
| Qwen Chromium DOM 回归（包括新增 4 项屏外按钮处理检查） | 50/50 |
| GLM 1.17.5 会话/文本 DOM 回归 | 18/18 |
| 新增生产适配器 + Chromium 双模型/延迟重试集成 | 3/3 |
| 原生产适配器长输入/双模型集成 | 3/3 |
| 原生产适配器 Qwen 重试集成 | 5/5 |
| npm run check、Python/新增 JS/启动脚本语法检查 | 通过 |

DOM 合计 96 项，生产适配器/CDP 集成合计 11 项。这些是检查数，不是 107 次线上账号请求。Agent 文件未修改，本轮未重跑其全集。

## 新增集成测试实际执行的流程

Qwen 完整提示为 55,300 个 UTF-16 代码单元 / 66,500 个 UTF-8 字节，以 14 次原生编辑填入，只有一次原始提交。首个模拟响应返回网络错误，长用户消息让重试按钮位于屏外；生产适配器确认当前回合后滚动、再次命中检查，点击一次重试，保留页面等待 600ms 延迟提交。合计 2 个生成请求，返回完整回答，没有第二次原始发送，也没有重复写入文本。

同轮 GLM 的编辑器在输入后才创建 Send 按钮。实际 CDP 输入、校验、按钮点击成功，发送早于 Qwen 完整回答结束，验证了第二模型未被第一个模型的完成等待阻塞。测试同时核对发送次数、输入一致性、请求数、页面保持和完整返回值。

网络测试验证旁路保存/标题请求、跨源不相关请求不再影响本轮状态；真正的 HTTP 429、流中断仍报错；重定向保留同一请求归因；请求分类不输出正文与鉴权信息。

## 基线对照

新增网络归因组与重试组在旧 1.17.7 源码下合计 36 项，26 通过、10 不通过；当前源码下该组全部通过。新增 GLM DOM 组在原基线下 2/13 通过，当前 13/13 通过。多条测试可能覆盖同一缺陷，不应将不通过数量解释为相同数量的独立线上故障。

## 可复现命令

```bash
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_glm_no_reload.cjs tests/test_launcher.cjs \
  tests/test_provider_layout.cjs tests/test_browser_import.cjs \
  tests/test_node_diagnostics.cjs tests/test_recovery_access.cjs \
  tests/test_progress_view.cjs tests/test_verification_detection.cjs \
  tests/test_upgrade1175.cjs tests/test_qwen_network_retry.cjs \
  tests/test_send_pipeline1177.cjs tests/test_network_attribution1178.cjs
npm run check
python tests/browser_send1178_dom.py --output /tmp/send1178-dom.json
python tests/browser_send1177_dom.py --output /tmp/send1177-dom.json
python tests/browser_qwen1176_smoke.py --output /tmp/qwen-dom.json
python tests/browser_glm1175_smoke.py --output /tmp/glm-dom.json
node tests/browser_send1178_adapter.cjs /tmp/send1178-adapter.json
node tests/browser_send1177_adapter.cjs /tmp/send1177-adapter.json
node tests/browser_qwen1176_adapter_smoke.cjs /tmp/qwen-adapter.json
```

可选浏览器测试需要 Python Playwright 和 `/usr/bin/chromium`，使用本地 HTML，不需要用户账号。测试中的 --no-sandbox 仅用于当前隔离容器，没有修改应用生产启动策略。

## 限制和未执行项

未执行真实 Qwen/GLM 账号、公网生成接口和完整 Electron 窗口测试。测试使用真实 Chromium、CDP 与生产 pageAction/WebsiteAdapter，但宿主布局、会话地址和 Network 通知是夹具；HTML-to-Markdown 在适配器集成中使用简单替身。普通单元测试也不是完整浏览器的等价替代。

npm ci 在本轮未完成；对 npm 官方 registry 和镜像的连接检查均报临时 DNS 解析失败，所需 JSDOM/Electron 依赖未安装。因此未运行完整 npm test 或 Electron GUI smoke，选定 Node 191 项不代表完整 npm 测试集。一次组合浏览器测试命令超出工具调用时限，未完成的 Qwen 集成组随后单独重跑，5/5 完成；未把中断的执行计入通过数。

逐项浏览器结果见 `TEST_EVIDENCE_1178.json`。发布时另将增量补丁实际应用到原始 1.17.7，并核对补丁结果、完整压缩包与发布目录的每个文件内容和执行权限。

## 发布包一致性

最终源码包共 249 个文件；相较原始 1.17.7 为 10 个修改文件、8 个新增文件，合计 18 个路径。全部 102 个 Agent 文件逐字节及执行权限保持一致。增量补丁已实际应用到干净的 1.17.7；补丁应用结果、压缩包解压结果和发布目录的 249 个文件及权限逐一一致。
