#!/usr/bin/env python3
"""Use the configured Qwen desktop-agent to translate the article in chunks."""
import html
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = Path('/home/yiye/tmp/fusion-10-tasks/lwn250967.html').read_text(encoding='utf-8', errors='replace')
src = src.split('<main>', 1)[-1].split('</main>', 1)[0]
src = re.sub(r'<(script|style|noscript)\b.*?</\1>', '', src, flags=re.I | re.S)
src = re.sub(r'<[^>]+>', ' ', src)
src = re.sub(r'\s+', ' ', html.unescape(src)).strip()
sentences = re.split(r'(?<=[.!?。！？])\s+', src)
chunks, current = [], ''
for sentence in sentences:
    if current and len(current) + len(sentence) + 1 > 7000:
        chunks.append(current); current = ''
    current = (current + ' ' + sentence).strip()
if current:
    chunks.append(current)

translated = []
for index, chunk in enumerate(chunks, 1):
    task = Path('/tmp') / f'lwn-qwen-{index}.txt'
    cached = Path('/tmp') / f'lwn-qwen-{index}.md'
    if cached.exists():
        translated.append(cached.read_text(encoding='utf-8', errors='replace').strip())
        print(f'using cached chunk {index}/{len(chunks)}', flush=True)
        continue
    task.write_text('将下面英文准确翻译为中文，只输出译文，不要解释，不要省略：\n' + chunk, encoding='utf-8')
    proc = subprocess.run([
        str(ROOT / 'run.sh'), 'run', '--no-auto-start', '--model', 'qwen',
        '--non-interactive', '--max-steps', '1', '--timeout', '600',
        '--task-file', str(task)], cwd=ROOT, text=True, capture_output=True, timeout=720)
    combined = proc.stdout + '\n' + proc.stderr
    match = re.search(r'结果文件：([^\n]+)', combined)
    if not match:
        candidates = sorted((ROOT / '.runtime/runs').glob('*/result.md'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise SystemExit(f'chunk {index} failed: {combined[-1500:]}')
        result_path = candidates[0]
    else:
        result_path = Path(match.group(1).strip())
    result = result_path.read_text(encoding='utf-8', errors='replace')
    result = re.sub(r'^# 任务结果.*?工具步数：\d+\s*', '', result, flags=re.S).strip()
    cached.write_text(result, encoding='utf-8')
    translated.append(result)
    print(f'translated chunk {index}/{len(chunks)}', flush=True)

out = Path('/home/yiye/tmp/fusion-10-tasks/lwn250967_zh_qwen.md')
header = ('# 每个程序员都应该了解的内存知识，第 1 部分\n\n'
          '> 来源：[LWN.net Articles/250967](https://lwn.net/Articles/250967/)\n'
          '> 使用当前 desktop-agent 配置的 Qwen 分块翻译；专有名词、代码和图表引用保留原意。\n\n')
out.write_text(header + '\n\n'.join(translated) + '\n', encoding='utf-8')
print(f'wrote {out} ({out.stat().st_size} bytes)')
