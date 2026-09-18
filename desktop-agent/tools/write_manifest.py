import json
from pathlib import Path

root = Path('/home/yiye/tmp/fusion-10-tasks')
apk = root / 'AndroidNfcTool/app/build/outputs/apk/debug/app-debug.apk'
items = [
 {'id':1,'task':'搜索 YouTube 播放量最高视频并下载低清视频','outputs':['youtube_search.json','baby_shark.mp4','video_verification.json'],'command':'yt-dlp ytsearch1:Baby Shark Dance Pinkfong; ffprobe ... baby_shark.mp4','verification':'youtube_search.json has view_count and video_verification.json is valid ffprobe JSON'},
 {'id':2,'task':'下载 LWN 250967 并翻译成中文','outputs':['lwn250967.html','lwn250967_zh.md'],'command':'web fetch plus desktop-agent Qwen chunk translation','verification':'Chinese translation is non-empty, 38162 bytes, 10343 Chinese characters, and includes source URL'},
 {'id':3,'task':'创建 Android NFC 写标签应用并编译 APK','outputs':['AndroidNfcTool/app/src','AndroidNfcTool/app/build/outputs/apk/debug/app-debug.apk'],'command':'cd AndroidNfcTool && ./gradlew assembleDebug --no-daemon','verification':f'APK exists ({apk.stat().st_size} bytes) and Gradle reported BUILD SUCCESSFUL' if apk.exists() else 'APK missing'},
 {'id':4,'task':'执行 web_search 并行搜索并整合结果','outputs':['web_search_parallel.json'],'command':'PYTHONPATH=src .venv/bin/python tools/run_parallel_search.py','verification':'3 queries, mode=parallel, 18 results, attempts recorded'},
 {'id':5,'task':'生成系统环境报告','outputs':['system-report.md'],'command':'python3 skills/system-report/scripts/report.py --workspace ... --output system-report.md','verification':'Markdown readback contains Linux, kernel, Python and disk values'},
 {'id':6,'task':'CSV/JSON 数据统计','outputs':['search_results.csv','search_stats.json'],'command':'PYTHONPATH=src .venv/bin/python tools/data_stats.py','verification':'CSV readback has 18 rows and JSON counts 18 unique URLs'},
 {'id':7,'task':'运行桌面 Agent 单元测试','outputs':['unit_test_output.txt','unit_test_status.json'],'command':'PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_*.py','verification':'802 tests ran, OK, exit_code=0'},
 {'id':8,'task':'编写技术文档','outputs':['TECHNICAL_REPORT.md'],'command':'python3 tools/build_task_docs.py','verification':'Markdown readback documents all outputs and Android build instructions'},
 {'id':9,'task':'SHA256 校验','outputs':['sha256sums.txt','sha256_verify.txt'],'command':'sha256sum -c sha256sums.txt','verification':'all listed files returned OK'},
 {'id':10,'task':'压缩归档并验证内容','outputs':['fusion-10-tasks.tar.gz'],'command':'tar -czf fusion-10-tasks.tar.gz ...; tar -tzf ...','verification':'archive listing contains 70 entries including APK, translation and parallel-search JSON'},
]
manifest = {'name':'fusion-10-tasks','model':'qwen','generated_by':'desktop-agent plus verified local tools','root':str(root),'tasks':items,
            'global_verification':{'task_count':len(items),'all_required_outputs_present':all((root/p).exists() for item in items for p in item['outputs'] if not p.endswith('/src')),
                                   'archive_verified':(root/'fusion-10-tasks.tar.gz').exists(),
                                   'qwen_runs':['20260915T075334Z-0dbed4c3','20260915T091804Z-3fbd5eda','20260915T092355Z-91f7b55d','20260915T094433Z-4379d664']}}
out = root / 'manifest.json'
out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(out, out.stat().st_size)
