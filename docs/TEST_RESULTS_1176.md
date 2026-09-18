# 1.17.6 实际测试记录

日期：2026-09-11。Linux；Python 3.13.5；Node v22.16.0；Chromium 144.0.7559.96。

| 实际执行检查 | 结果 |
| --- | --- |
| 新增 Qwen 重试流程单元测试 | 18 / 18 通过，已包含在下一行 158 项中 |
| 选定 Node 回归测试 | 158 / 158 通过，0 跳过 |
| 父应用 Python 全集 | 126 / 126 通过 |
| 真实 Chromium DOM 场景 | 46 / 46 通过 |
| 相同 Chromium DOM 场景运行于原始 1.17.5 | 30 / 46 通过，16 项失败；含用户报告的网络错误卡、无 alert 包装、局部页脚按钮等 |
| 生产适配器 + 真实 Chromium CDP 输入 + DOM | 5 / 5 通过；页面、会话路由及网络通知为本地模拟数据 |
| JavaScript、Python、启动脚本语法检查 | 通过 |
| Agent 源码 | 与 1.17.5 保持字节一致，版本仍是 0.16.1；本次没有重跑 Agent 全集，不沿用历史测试数作为本次结果 |

## Node 测试

```bash
node --test tests/test_qwen_network_retry.cjs
node --test tests/test_glm_no_reload.cjs tests/test_launcher.cjs \
  tests/test_provider_layout.cjs tests/test_browser_import.cjs \
  tests/test_node_diagnostics.cjs tests/test_recovery_access.cjs \
  tests/test_progress_view.cjs tests/test_verification_detection.cjs \
  tests/test_upgrade1175.cjs tests/test_qwen_network_retry.cjs
```

新增 18 项测试执行真实生产 `adapter.cjs`、网络计数与截止时间逻辑；DOM/CDP 边界和纯文本转换由夹具模拟。场景包含 HTTP 200 应用层错误、503 后迟到错误卡、迟到按钮、原响应仍在结束、点击回执丢失、第二次失败不重试、人工请求成功/失败、等待输入占用时的竞争、按钮预留时的竞争、会话/草稿/模型变化、取消、候选总时限、关闭恢复和普通成功回答。模拟计时器加速测试，不需要 npm 包或真实模型。

## Chromium 页面与实际点击

需要环境已安装 Python Playwright 和 `/usr/bin/chromium`；不是应用新增运行依赖。

```bash
python tests/browser_qwen1176_smoke.py --output /tmp/qwen-dom.json
node tests/browser_qwen1176_adapter_smoke.cjs /tmp/qwen-cdp.json
```

46 项页面场景执行生产 `pageAction`，使用真实 DOM 选择器、可见性、几何位置和 `elementFromPoint`。以 `page.set_content` 创建模拟页面，注入模拟的会话 URL，不访问 Qwen。DOM 检查和一次性预留过程始终没有触发 DOM 点击。

后 5 项将生产 WebsiteAdapter 与上述页面代码串联，用真实 Chromium `Input.insertText` 和鼠标 CDP 命令填写、点击模拟页面。每项只填写原问题一次、只点击发送一次；网络错误成功恢复的三项各点击重试一次，人工先恢复的一项零次自动点击，第二次仍失败的一项在一次点击后明确超时。没有刷新。

这 5 项的会话路由与 `Network.*` 通知是夹具生成的，而非真实 Qwen HTTP 流量；HTML 转纯文本是测试替身，不验证 Turndown 格式。测试不使用 Electron 窗口，不证明实站登录、真实服务器恢复或实际双模型整合。最初尝试导航到本地测试地址被受控 Chromium 以 `ERR_BLOCKED_BY_ADMINISTRATOR` 阻止，最终测试改为无需导航的 `set_content` 模拟页面，没有调整浏览器管理策略。

## Python 与语法

```bash
python -m unittest discover -s tests -p 'test_*.py'
npm run check
node --check tests/test_qwen_network_retry.cjs
node --check tests/browser_qwen1176_adapter_smoke.cjs
python -m py_compile tests/browser_qwen1176_smoke.py tests/browser_qwen1176_worker.py backend/app.py
bash -n run.sh desktop-agent/run.sh desktop-agent/setup.sh
```

父应用 126 项包含候选共同截止时间、默认全模型完成屏障与恢复进度相关测试。Agent 源码和本地 JSON 纠正代码没有修改。

## 未验证项目

尝试安装 jsdom、turndown 等测试依赖时 registry.npmjs.org DNS 返回 `EAI_AGAIN`。因此没有执行依赖 npm 包的完整 `npm test` / JSDOM 全集或 Electron 图形端到端测试；上面的 158 项是明确列出的选定回归测试，不能称为全部 Node 测试。

没有真实 Qwen 账号，没有本次错误现场的 DOM 或截图，也没有测试公网模型推理。页面样式、控件结构随站点变化时，仍可能需要调整选择器。

## 交付核对

完整包保留顶层 `multillm-fusion/`。增量补丁只对应原始 1.17.5 完整包，在新解压的基线上实际应用；交付脚本对补丁应用结果和完整包分别解压后的全部文件路径、文件字节进行比较，并验证 `desktop-agent/` 与基线一致。压缩包不含本次临时环境、测试日志、浏览器配置目录或 npm 缓存。
