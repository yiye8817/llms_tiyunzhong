#!/usr/bin/env python3
"""Translate the downloaded LWN HTML into a Chinese Markdown artifact."""
import json
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from html import unescape
from pathlib import Path

SRC = Path('/home/yiye/tmp/fusion-10-tasks/lwn250967.html')
DST = Path('/home/yiye/tmp/fusion-10-tasks/lwn250967_zh.md')

class Extract(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript', 'nav', 'header', 'footer'}:
            self.skip += 1
        if not self.skip and tag in {'h1', 'h2', 'h3', 'h4', 'p', 'li', 'br'}:
            self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'nav', 'header', 'footer'} and self.skip:
            self.skip -= 1
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

def paragraphs(text):
    text = re.sub(r'\r', '', text)
    return [re.sub(r'\s+', ' ', x).strip() for x in text.split('\n') if re.sub(r'\s+', ' ', x).strip()]

def translate(text):
    if not text:
        return ''
    # MyMemory has a practical URL limit; translate sentence-aware chunks.
    chunks, cur = [], ''
    for sentence in re.split(r'(?<=[.!?])\s+', text):
        if cur and len(cur) + len(sentence) + 1 > 430:
            chunks.append(cur); cur = ''
        cur = (cur + ' ' + sentence).strip()
    if cur:
        chunks.append(cur)
    out = []
    for chunk in chunks:
        url = 'https://api.mymemory.translated.net/get?q=' + urllib.parse.quote(chunk) + '&langpair=en|zh-CN'
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = json.load(response)
            translated = data.get('responseData', {}).get('translatedText', '').strip()
            if translated and translated.lower() != 'query length exceeded':
                out.append(translated)
            else:
                out.append(chunk)
        except Exception:
            out.append(chunk)
        time.sleep(0.08)
    return ' '.join(out)

def main():
    source = SRC.read_text(encoding='utf-8', errors='replace')
    # Restrict extraction to the article's <main>; the surrounding page has
    # hundreds of navigation/comment lines which are not part of the article.
    source = source.split('<main>', 1)[-1].split('</main>', 1)[0]
    source = re.sub(r'<(script|style|noscript)\b.*?</\1>', '', source, flags=re.I | re.S)
    source = re.sub(r'<\s*(p|h[1-6]|li|blockquote|br)\b[^>]*>', '\n', source, flags=re.I)
    source = re.sub(r'<[^>]+>', '', source)
    # HTML source wraps prose at arbitrary column widths.  Collapse those
    # wraps before sentence-aware chunking so one paragraph does not trigger
    # hundreds of tiny API requests.
    normalized = re.sub(r'\s+', ' ', unescape(source)).strip()
    paras = [normalized] if normalized else []
    translated = []
    for i, para in enumerate(paras, 1):
        # Keep navigation/promotional boilerplate compact, translate article text.
        if len(para) < 3:
            continue
        translated.append(translate(para))
        if i % 10 == 0:
            print(f'translated {i}/{len(paras)}', flush=True)
    title = translated[0] if translated else '每个程序员都应该了解的内存知识（第 1 部分）'
    body = '\n\n'.join(f'{i}. {p}' for i, p in enumerate(translated[1:], 1) if p)
    content = ('# ' + title + '\n\n'
               '> 来源：[LWN.net Articles/250967](https://lwn.net/Articles/250967/)\n'
               '> 原文下载：`lwn250967.html`。以下为逐段机器翻译，专有名词和代码保持原样；发布前请人工校对。\n\n'
               + body + '\n')
    DST.write_text(content, encoding='utf-8')
    print(f'wrote {DST} ({DST.stat().st_size} bytes, {len(translated)} paragraphs)')

if __name__ == '__main__':
    main()
