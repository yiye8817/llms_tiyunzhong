#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path
from fusion_agent.web_search import WebSearchTools

queries = [
    'Linux kernel memory performance CPU cache NUMA DMA',
    'Android NFC NDEF write tag best practices',
    'Qwen web agent JSON tool calling reliability',
]
events = []
tool = WebSearchTools(event=lambda name, fields: events.append({'event': name, **fields}))
reports = []
for query in queries:
    result = tool.search({'query': query, 'mode': 'parallel', 'max_results': 6, 'timeout_seconds': 20})
    reports.append(result)
payload = {
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'mode': 'parallel', 'queries': queries, 'reports': reports, 'events': events,
    'verification': {'all_reports_have_attempts': all(bool(r.get('attempts')) for r in reports),
                     'result_count': sum(r.get('result_count', 0) for r in reports)},
}
out = Path('/home/yiye/tmp/fusion-10-tasks/web_search_parallel.json')
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'path': str(out), 'queries': len(queries), 'results': payload['verification']['result_count']}, ensure_ascii=False))
