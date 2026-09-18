'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { extractReply, preserveJSONReply, structuredReplyState } = require('../electron/reply-extraction.cjs');
const answer = rawText => ({ rawText, html: '<pre>fixture</pre>', jsonTextSafe: true, codeLanguages: ['json'] });
const raw = '{"type":"action","tool":"python.run","arguments":{"code":"print("hello")\\n"},"summary":"运行测试"}';

test('malformed Python transport reaches local parser byte-for-byte without Markdown conversion', () => {
  let conversions = 0;
  const result = extractReply(answer(raw), () => { conversions++; return 'CORRUPTED'; });
  assert.equal(result.content, raw); assert.equal(result.method, 'json_raw_unvalidated');
  assert.equal(conversions, 0); assert.throws(() => JSON.parse(result.content));
});
test('malformed final answer quotes are preserved, not repaired by server', () => {
  const text = '{"type":"final","answer":"使用 "Prompt as Code"。"}';
  assert.equal(preserveJSONReply(answer(text)).content, text);
});
test('a whole JSON fence may carry the exact malformed payload', () => {
  const result = preserveJSONReply(answer('```json\n' + raw + '\n```'));
  assert.equal(result.content, raw); assert.equal(result.method, 'json_raw_unvalidated');
});
test('correct JSON still uses the strict JSON path', () => {
  const text = JSON.stringify({type:'action',tool:'python.run',arguments:{code:'print("x")\n'},summary:'test'});
  assert.equal(preserveJSONReply(answer(text)).method, 'json_code');
  assert.equal(preserveJSONReply(answer(text)).content, text);
});
for (const [name, mutation] of [
  ['unsafe rendered semantics', {jsonTextSafe:false}],
  ['Python code block', {codeLanguages:['python']}],
  ['multiple code blocks', {codeLanguages:['json','json']}],
]) test(`${name} is not promoted to a protocol object`, () => {
  assert.equal(preserveJSONReply({...answer(raw),...mutation}), null);
});
test('prose plus an example is not extracted into an action', () => {
  assert.equal(preserveJSONReply(answer('Example only:\n' + raw)), null);
  assert.equal(preserveJSONReply(answer(raw + '\nDo this next.')), null);
});
test('multiple objects are never reduced to the first object', () => {
  const text = raw + '\n{"type":"final","answer":"other"}';
  const result=preserveJSONReply(answer(text));
  assert.equal(result.content,text);assert.throws(()=>JSON.parse(result.content));
});
test('unterminated object stays pending, no guessed closing syntax', () => {
  const fragment='{"type":"action","tool":"python.run","arguments":{"code":"print(';
  assert.equal(preserveJSONReply(answer(fragment)),null);
  assert.equal(structuredReplyState(answer(fragment)).incomplete,true);
});
test('closed malformed source is not misclassified as waiting for more bytes', () => {
  assert.equal(structuredReplyState(answer(raw)).incomplete,false);
  assert.equal(structuredReplyState(answer(raw)).reason,'invalid_json_syntax');
});
