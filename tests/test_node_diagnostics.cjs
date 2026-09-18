'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createLogger, redactText, lineSink, sanitize } = require('../electron/diagnostics.cjs');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'fusion-log-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

test('same redacted event reaches terminal and private disk file', t => {
  const root = fixture(t), lines = [];
  const log = createLogger({ directory: root, getSecrets: () => ['fixture-secret'], terminal: line => lines.push(line) });
  log('job_complete', { job_id: 'fixture-job', prompt: 'private question', markdown: 'private output', markdown_chars: 21, detail: 'fixture-secret Bearer bearer-value', nested: { api_key: 'hidden' } });
  const saved = fs.readFileSync(log.file, 'utf8');
  assert.equal(saved, lines.join(''));
  for (const secret of ['private question', 'private output', 'fixture-secret', 'bearer-value', 'hidden']) assert.ok(!saved.includes(secret));
  const row = JSON.parse(saved);
  assert.equal(row.markdown_chars, 21);
  assert.equal(row.job_id, 'fixture-job');
  assert.equal(fs.statSync(log.file).mode & 0o777, 0o600);
});

test('log rotation retains bounded backups and records each new event', t => {
  const root = fixture(t);
  const log = createLogger({ directory: root, terminal: () => {}, maxBytes: 200, backups: 2 });
  for (let i = 0; i < 8; i++) log('stage_change', { phase: 'x'.repeat(80), index: i });
  assert.deepEqual(fs.readdirSync(root).sort(), ['electron.log', 'electron.log.1', 'electron.log.2']);
  assert.equal(JSON.parse(fs.readFileSync(log.file, 'utf8')).index, 7);
});

test('log file symlink cannot redirect writes and warning only occurs once', t => {
  const root = fixture(t), output = [];
  const target = path.join(root, 'target'); fs.writeFileSync(target, 'untouched');
  fs.symlinkSync(target, path.join(root, 'electron.log'));
  const log = createLogger({ directory: root, terminal: line => output.push(line) });
  log('test'); log('test');
  assert.equal(fs.readFileSync(target, 'utf8'), 'untouched');
  assert.equal(output.filter(line => line.includes('日志文件写入失败')).length, 1);
});

test('redaction covers headers URL credentials query tokens and quoted known secrets', () => {
  const input = 'https://name:pass@example.test/path?token=abc&ok=1 Cookie: sid=cookie-value\nAuthorization: Bearer abc-def\nkey="prefix\\nsecret"';
  const clean = redactText(input, ['prefix\nsecret']);
  for (const secret of ['name:pass', 'token=abc', 'cookie-value', 'abc-def', 'prefix']) assert.ok(!clean.includes(secret));
  assert.ok(clean.includes('&ok=1'));
  assert.equal(sanitize({ optional: undefined }).optional, undefined);
});

test('line buffering handles split Unicode and secrets before redacting', () => {
  const lines = [], sink = lineSink(line => lines.push(redactText(line, ['fixture-secret'])));
  const input = Buffer.from('中文 fixture-secret\nlast line');
  sink.write(input.subarray(0, 2)); sink.write(input.subarray(2, 12)); sink.write(input.subarray(12)); sink.end();
  assert.deepEqual(lines, ['中文 [REDACTED]', 'last line']);
});

test('oversized raw line is discarded in full and later lines remain available', () => {
  const lines = [], sink = lineSink(line => lines.push(line), 10);
  sink.write('sensitive-'); sink.write('suffix-more\nok\n'); sink.end();
  assert.deepEqual(lines, ['[oversized diagnostic line omitted]', 'ok']);
});

test('explicit payload logs preserve all content across chunks while masking credentials', t => {
  const root = fixture(t), lines = [];
  const log = createLogger({ directory: root, getSecrets: () => ['actual-local-key'], terminal: line => lines.push(line), chunkChars: 48 });
  log('adapter.start', { request_id: 'r1', payload: { prompt: '中文\n完整输入 '.repeat(50), api_key: 'actual-local-key', nested: { password: 'hidden-pass' }, answer: 'Bearer remote-token' } });
  assert.equal(fs.readFileSync(log.file, 'utf8'), lines.join(''));
  const rows = lines.map(line => JSON.parse(line));
  assert.ok(rows.length > 1);
  const body = JSON.parse(rows.map(row => row.payload).join(''));
  assert.equal(body.prompt, '中文\n完整输入 '.repeat(50));
  assert.equal(body.api_key, '[REDACTED]');
  assert.equal(body.nested.password, '[REDACTED]');
  for (const secret of ['actual-local-key', 'hidden-pass', 'remote-token']) assert.ok(!lines.join('').includes(secret));
  assert.ok(rows.every((row, i) => row.part === i + 1 && row.parts === rows.length && !row.truncated && row.payload_id === rows[0].payload_id));
});

test('metadata-only mode omits payload and content bounds report truncation', t => {
  const root = fixture(t), lines = [];
  const log = createLogger({ directory: root, terminal: line => lines.push(line), content: false });
  log('request', { payload: { prompt: 'sensitive-question' } });
  assert.equal(JSON.parse(lines[0]).payload_omitted, true);
  assert.ok(!lines[0].includes('sensitive-question'));
  const bounded = [];
  createLogger({ directory: root, filename: 'bounded.log', terminal: line => bounded.push(JSON.parse(line)), maxContentChars: 20, chunkChars: 10 })('request', { payload: { text: 'a'.repeat(100) } });
  assert.ok(bounded.every(row => row.truncated && row.parts === 2));
  assert.equal(bounded.map(row => row.payload).join('').length, 20);
});

test('structured content is forwarded intact without second redaction or line dropping', t => {
  const root = fixture(t), lines = [];
  const log = createLogger({ directory: root, terminal: line => lines.push(line) });
  const record = JSON.stringify({ component: 'backend', event: 'reply', payload: 'x'.repeat(24000) + 'Cookie: [REDACTED]' });
  const sink = lineSink(line => log.forward(line));
  sink.write(record.slice(0, 7000)); sink.write(record.slice(7000) + '\n'); sink.end();
  assert.equal(lines.join(''), record + '\n');
  assert.deepEqual(JSON.parse(lines[0]), JSON.parse(record));
});

test('embedded quoted secrets are masked through whitespace and escaped quotes', () => {
  for (const source of ['password="FIRST SECRET-TAIL"', "password='FIRST SECRET-TAIL'", JSON.stringify({ password: 'FIRST"SECRET-TAIL' })]) {
    const clean = redactText(source);
    assert.ok(!clean.includes('FIRST'));
    assert.ok(!clean.includes('SECRET-TAIL'));
  }
});
