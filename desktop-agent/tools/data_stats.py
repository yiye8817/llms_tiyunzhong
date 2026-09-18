#!/usr/bin/env python3
import csv, json
from collections import Counter
from pathlib import Path

root = Path('/home/yiye/tmp/fusion-10-tasks')
source = json.loads((root / 'web_search_parallel.json').read_text(encoding='utf-8'))
rows = []
for report in source['reports']:
    for result in report.get('results', []):
        rows.append({'query': report['query'], 'source': result.get('source',''),
                     'title': result.get('title',''), 'url': result.get('url','')})
csv_path = root / 'search_results.csv'
with csv_path.open('w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['query','source','title','url'])
    writer.writeheader(); writer.writerows(rows)
stats = {'rows': len(rows), 'queries': len(set(r['query'] for r in rows)),
         'sources': dict(Counter(r['source'] for r in rows)),
         'nonempty_urls': sum(bool(r['url']) for r in rows),
         'unique_urls': len(set(r['url'] for r in rows))}
json_path = root / 'search_stats.json'
json_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'csv': str(csv_path), 'json': str(json_path), 'stats': stats}, ensure_ascii=False))
