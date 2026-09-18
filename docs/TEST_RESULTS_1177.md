# 1.17.7 实测记录

执行日期：2026-09-11。基线是完整 1.17.6；Python 3.13.5，Node 22.16.0，Chromium 144.0.7559.96。所有测试在当前工作过程中实际运行，未使用用户的网页账号、用户 API 凭据或实时 Qwen / GLM 服务。

## 结果

| 检查 | 实际结果 |
|---|---:|
| 父应用 Python 全集，含 4 项新增 HTTP/配置测试 | 130/130 通过 |
| 选定 Node 回归及 15 项新增输入链测试 | 173/173 通过 |
| 新增 Chromium DOM/原生输入检查 | 15/15 通过 |
| GLM 既有会话/输入 DOM 回归 | 18/18 通过 |
| Qwen 既有重试 DOM 回归 | 46/46 通过 |
| 新增生产适配器 + CDP 双模型检查 | 3/3 通过 |
| Qwen 既有生产适配器 + CDP 重试检查 | 5/5 通过 |
| npm run check | 通过 |
| Python、新增 Node 测试和启动脚本语法检查 | 通过 |

DOM 类合计 79 项，生产适配器/CDP 类合计 8 项。合计仅用于描述检查覆盖，不等于 87 次线上调用。Agent 的 102 个文件与基线逐字节相同；本轮未重跑其全集，不复用历史通过数冒充新结果。

## 覆盖与限制

后端测试经过实际 FastAPI TestClient、WebSocket Bridge 和 Engine 调度，网页结果由可控应答器返回。验证单 Qwen 与 Qwen+GLM 候选收到完全一致的完整系统/历史/用户内容，GLM 确实收到 generate 包，整合调用在候选之后；参数可保存且被原样转发。

新增 Node 测试执行生产 WebsiteAdapter 代码，但用模拟页面/计时器、网络事件以及纯文本转换器替身，验证隐藏页面准备、完整长提示分段、前缀变化停止、异步提交可见性保持、忽略点击不补点和共享时限。

Chromium DOM 夹具包含 GLM bare contenteditable、无 role ProseMirror/tiptap、包装层、英文 textarea、回复中的假编辑器、歧义编辑器、搜索对话框、maxlength、光标改变、前缀改变、禁用发送按钮及先隐藏再显示的编辑器。长文本样本是 59,000 Unicode 码点 / 60,000 UTF-16 代码单元 / 66,000 UTF-8 字节；纯文本分段核验，富文本一次原生写入完整核验。

生产适配器/CDP 双模型检查：Qwen 的 55,300 个 UTF-16 代码单元长提示采用 14 次原生编辑并只提交一次；GLM 隐藏富文本编辑器显示后完成一次原生输入、一次提交。页面异步提交延迟 400ms，模拟响应再延迟 3 秒。验证 GLM 发送发生在 Qwen 回答完成之前，两个完整结果均返回。每次原生多行编辑可能触发多个 DOM input 事件，测试不把事件次数误当作提交次数。

这些集成测试使用真实 Chromium、实际 CDP Input 方法、生产 pageAction 和 WebsiteAdapter，但路由、网络通知、服务器结果及宿主显示权限为夹具，HTML-to-Markdown 转换使用替身。没有真实公网 HTTP、Electron 原生窗口或真实账号，因此不证明线上网络错误已经消失。

同一新增 DOM 组在旧 1.17.6 下是 3/15 通过。12 项不通过中包含基线没有新 inputProgress API 的检查；不能把全部 12 项都称作 12 个独立线上故障。升级后同组 15/15 通过。

## 复现命令

在项目根目录执行：

```bash
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_glm_no_reload.cjs tests/test_launcher.cjs \
  tests/test_provider_layout.cjs tests/test_browser_import.cjs \
  tests/test_node_diagnostics.cjs tests/test_recovery_access.cjs \
  tests/test_progress_view.cjs tests/test_verification_detection.cjs \
  tests/test_upgrade1175.cjs tests/test_qwen_network_retry.cjs \
  tests/test_send_pipeline1177.cjs
npm run check
python tests/browser_send1177_dom.py --output /tmp/send1177-dom.json
python tests/browser_glm1175_smoke.py --output /tmp/glm1175-dom.json
python tests/browser_qwen1176_smoke.py --output /tmp/qwen1176-dom.json
node tests/browser_send1177_adapter.cjs /tmp/send1177-adapter.json
node tests/browser_qwen1176_adapter_smoke.cjs /tmp/qwen1176-adapter.json
```

可选 Chromium 测试需要 Python Playwright 和 `/usr/bin/chromium`，不需要账号。默认是无头本地夹具；浏览器测试用 `--no-sandbox` 适应当前 root 容器，此选项不改变应用生产启动脚本。未安装依赖时不应将跳过测试记录为通过。

## 未执行项目

完整 `npm test` 的 JSDOM/转换器依赖和完整 Electron 图形运行未执行。尝试安装依赖失败，npm 报 `EAI_AGAIN getaddrinfo registry.npmjs.org`。上述选定测试不代表完整 npm 测试集。未进行登录、验证码处理、账号限流或当前公网服务端点验证。

包内 `TEST_EVIDENCE_1177.json` 保留浏览器测试的逐项结果与范围说明，不包含个人运行日志。最终包/补丁另做逐文件一致性检查。
