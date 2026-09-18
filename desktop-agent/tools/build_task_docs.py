from pathlib import Path
from datetime import datetime, timezone

root = Path('/home/yiye/tmp/fusion-10-tasks')
text = f'''# Fusion 10 项任务技术文档

生成时间（UTC）：{datetime.now(timezone.utc).isoformat()}

## 目录

- `youtube_search.json` 与 `baby_shark.mp4`：yt-dlp 搜索结果、视频下载及 ffprobe 核验。
- `lwn250967.html` 与 `lwn250967_zh.md`：LWN 原文和中文机器翻译；翻译保留来源链接并标明需人工校对。
- `AndroidNfcTool/`：Android Kotlin NFC NDEF 写标签应用；Debug APK 位于 `app/build/outputs/apk/debug/app-debug.apk`。
- `web_search_parallel.json`：三个查询以 `mode=parallel` 调用 DDG/OpenCLI 等后端后合并的结果和尝试记录。
- `system-report.md`：系统、Python、内核和磁盘报告。
- `search_results.csv`、`search_stats.json`：搜索结果的 CSV/JSON 统计。
- `unit_test_output.txt`、`unit_test_status.json`：桌面 Agent 的 802 个单元测试结果。
- `sha256sums.txt`：所有交付物的 SHA-256 校验。

## Android NFC 写标签

在 Android 设备启用 NFC 后安装 Debug APK，打开应用输入文本或 URL，点击写入并将标签靠近手机。应用使用 NDEF 文本/URI record，写入前检查标签是否可写以及容量，写入成功后显示结果；不支持 NDEF 的标签会给出明确错误。构建命令：

    cd AndroidNfcTool && ./gradlew assembleDebug --no-daemon

构建现场已启用 `android.useAndroidX=true` 与 `android.enableJetifier=true`，并通过 Gradle 输出 `BUILD SUCCESSFUL` 核验。

## 可复现性

所有命令均在当前用户权限下运行。网络搜索结果属于时点数据，`web_search_parallel.json` 保存后端状态和时间戳；YouTube 播放量会随时间变化，应以 JSON 中记录的查询时间为准。
'''
out = root / 'TECHNICAL_REPORT.md'
out.write_text(text, encoding='utf-8')
print(out, out.stat().st_size)
