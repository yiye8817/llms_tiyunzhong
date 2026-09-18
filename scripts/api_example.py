#!/usr/bin/env python3
"""OpenAI Python SDK example. pip install openai; start desktop and log in first."""
import os
import argparse
from pathlib import Path
from openai import OpenAI

parser = argparse.ArgumentParser(description="调用本机网页模型；model 可选 web-fusion/chatgpt/deepseek/qwen/glm/kimi 等已启用模型")
parser.add_argument('--model', default='web-fusion', help='web-fusion 整合，或 chatgpt/deepseek/qwen/glm/kimi/web-<provider-id> 单独调用')
args = parser.parse_args()

data_dir = Path(os.environ.get("FUSION_DATA_DIR", "~/.local/share/multillm-fusion")).expanduser()
token = os.environ.get("FUSION_TOKEN") or (data_dir / "api-key.txt").read_text().strip()
client = OpenAI(
    base_url=f"http://127.0.0.1:{os.environ.get('FUSION_PORT', '8765')}/v1",
    api_key=token,
    timeout=600.0,
    max_retries=0,  # A retry can start another webpage conversation.
)
messages = [{"role": "user", "content": "如何系统分析 Android native 内存泄漏？给出步骤和命令。"}]
response = client.chat.completions.create(model=args.model, messages=messages)
answer = response.choices[0].message.content
print(answer)

# Subsequent turns carry the full conversation, as with Chat Completions.
messages += [{"role": "assistant", "content": answer}, {"role": "user", "content": "请进一步解释 DMA-BUF 泄漏的定位。"}]
stream = client.chat.completions.create(model=args.model, messages=messages, stream=True)
for chunk in stream:
    if chunk.choices:
        print(chunk.choices[0].delta.content or "", end="", flush=True)
print()
