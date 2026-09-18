# 1.17.10 / Agent 0.17.0 — 实测记录

本次测试在工作容器实际执行。没有使用真实 Qwen、GLM 登录账号，也没有把模拟网页或网络通知写作线上成功。

| 检查 | 结果 | 实际范围 |
|---|---:|---|
| 父应用 Python 全集 | 137/137 | 含3项新增进度/桥接测试；实际本地 web-fusion 接口验证默认重试配置、来源和60秒共享预算 |
| 选定 Node 回归 | 197/197 | 14个测试文件；含22项新错误封装/进度/网络追踪用例和已有保护回归，无跳过/失败 |
| Chromium DOM 回归 | 96/96 | GLM18、Qwen50、1177输入15、1178输入13 |
| 生产适配器 + Chromium/CDP | 14/14 | 既有三级恢复7、长输入/双模型3、本次故障4 |
| JavaScript / Python / shell语法 | 通过 | npm run check；父应用及测试29个Python文件AST；两个启动脚本bash -n |
| Agent文件一致性 | 103个文件未改动 | 本次不重复执行789项Agent全集，不把旧结果记作本次实测 |

## 旧版复现与修复验证

同一套真实浏览器测试加载未修改的1.17.9适配器和页面处理代码，以下4场景全部失败；加载本版全部通过。旧版失败是预期的对照实验，不计入本版通过数。

| 场景 | 旧版 | 本版 |
|---|---|---|
| 502结束后Stop仍在，前两级未产生请求 | 卡住/未完成 | 三级依次出现，人工点击后新生成并完成 |
| 当前错误卡没有assistant/alert属性 | 未完成 | 三级依次出现并完成 |
| HTTP200中明确错误封装，页面为普通占位文字 | 没有正确恢复 | 本地确认错误；不盲点未绑定控件；人工重试后完成，不学习未核验控件 |
| 当前错误卡只有图标，页面不接受非可信点击 | 截图阶段未匹配 | 第一级无效后截图定位，真实CDP点击成功，无需人工补点 |

新增场景都断言原问题只发送一次、原文完整、最终仅2次生成请求（原失败请求和成功重试），未刷新网页、未因切走页面丢失提交。人工恢复由测试驱动模拟，不是实际用户账号操作。

真实浏览器为Chromium144.0.7559.96。输入、DOM、屏幕像素和CDP鼠标事件是真实的；Qwen HTML、会话URL和Network通知是受控的。适配器测试使用轻量Turndown替身加载生产逻辑，不将这些场景当作Markdown转换测试或完整Electron GUI测试。

20秒默认人工等待通过配置和时钟测试验证；浏览器故障场景为缩短测试使用1–3秒设置。前一级已触发新请求时不会继续后续点击；旧错误、正常回答里的502/错误JSON示例、已变化用户消息、真实在途生成等保护继续回归。

## 本次执行命令

```bash
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_browser_import.cjs tests/test_launcher.cjs tests/test_network_attribution1178.cjs tests/test_node_diagnostics.cjs tests/test_progress_view.cjs tests/test_provider_layout.cjs tests/test_qwen_network_retry.cjs tests/test_qwen_retry1179.cjs tests/test_recovery_access.cjs tests/test_send_pipeline1177.cjs tests/test_upgrade1175.cjs tests/test_verification_detection.cjs tests/test_retry11710.cjs tests/test_response_error11710.cjs
python tests/browser_glm1175_smoke.py --output /tmp/glm11710.json
python tests/browser_qwen1176_smoke.py --output /tmp/qwen11710.json
python tests/browser_send1177_dom.py --output /tmp/send1177.json
python tests/browser_send1178_dom.py --output /tmp/send1178.json
node tests/browser_retry1179_adapter.cjs /tmp/retry1179.json
node tests/browser_send1178_adapter.cjs /tmp/longsend.json
node tests/browser_retry11710_adapter.cjs /tmp/retry11710.json
npm run check
bash -n run.sh desktop-agent/run.sh
```

浏览器专项需要Python Playwright、Pillow和系统`/usr/bin/chromium`；代码不会自动安装或登录。可用`FUSION_TEST_CASE=icon-native`单独运行新增场景，用`FUSION_TEST_SOURCE_ROOT=/原始1.17.9目录`做基线对照。结构化结果摘要与原始报告哈希见`TEST_EVIDENCE_11710.json`。

## 限制

npm依赖安装出现`EAI_AGAIN registry.npmjs.org`，完整npm测试、Electron/JSDOM图形测试未执行。真实Qwen/GLM账号未完成联网端到端验证。测试证明上述本地检测/恢复链路能够工作，不保证线上所有页面变更、代理问题或上游502都可以自动恢复。

本次没有增加现场日志输入；发布包不包含用户配置、登录信息、历史运行日志、截图、node_modules或虚拟环境。原有HTTP请求体日志和隐私设置保持原样；新增HTTP200错误检查不保存错误正文。

## 发布校验

补丁在全新1.17.9基线上实际应用，并按路径、字节内容和文件权限与发布目录逐一比较；最终tar.gz解压后再次比较。解压后重新执行本次22项Node专项、3项Python专项、JavaScript语法和父/子版本检查。文件数量及变更路径见`MODIFIED_FILES_11710.txt`。
