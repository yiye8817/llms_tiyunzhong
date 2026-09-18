'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const {responseError,RESPONSE_ERROR_LIMIT} = require('../electron/response-error.cjs');
const {webProgress} = require('../electron/web-progress.cjs');
for (const [name, body, status] of [
  ['HTTP-200 JSON error', '{"error":{"code":502,"message":"PRIVATE MESSAGE"}}',502],
  ['HTTP-200 failed envelope','{"success":false,"code":503,"message":"upstream"}',503],
  ['explicit failed envelope with null error','{"success":false,"error":null,"code":503}',503],
  ['SSE explicit error event','event: error\ndata: {"code":502,"message":"private"}\n\n',502],
  ['SSE final error envelope','data: {"error":{"status":504}}\n\ndata: [DONE]\n\n',504],
  ['CRLF SSE error','event: error\r\ndata: {"code":502}\r\n\r\n',502],
]) test(name,()=>{const row=responseError(body,'text/event-stream');assert.equal(row.status,status);assert.doesNotMatch(JSON.stringify(row),/PRIVATE|upstream|private/);});
for (const [name,body] of [
  ['normal answer','{"choices":[{"delta":{"content":"network error 502"}}]}'],
  ['generated JSON example','{"choices":[{"message":{"content":"{\\"error\\":true}"}}]}'],
  ['error prose','Oops! There was an issue connecting to Qwen3.8-Max.网络错误'],
  ['non-JSON body','<html>502</html>'],
  ['empty error','{"error":null}'],
  ['empty error object','{"error":{}}'],
  ['completed SSE','data: {"choices":[{"delta":{"content":"502"}}]}\n\ndata: [DONE]\n\n'],
  ['too large', 'x'.repeat(RESPONSE_ERROR_LIMIT+1)],
]) test('never retries merely because of '+name,()=>assert.equal(responseError(body,'text/event-stream'),null));
test('named DOM step reaches interface progress',()=>assert.deepEqual(webProgress('adapter.retry_stage_started',{stage:'dom_handler'}),{stage:'retrying',retry_stage:'dom_handler'}));
test('named screenshot step reaches interface progress',()=>assert.deepEqual(webProgress('adapter.retry_stage_started',{stage:'screenshot_click'}),{stage:'retrying',retry_stage:'screenshot_click'}));
test('manual countdown reaches interface progress',()=>assert.deepEqual(webProgress('adapter.retry_manual_wait',{remaining_seconds:20}),{stage:'manual_retry_required',retry_stage:'manual',remaining_seconds:20}));
test('rating-blocked manual send countdown reaches interface progress',()=>assert.deepEqual(webProgress('adapter.qwen_manual_send_wait',{remaining_seconds:20}),{stage:'manual_retry_required',retry_stage:'manual_send',remaining_seconds:20}));
test('detected human send reaches recovery progress',()=>assert.deepEqual(webProgress('adapter.qwen_manual_send_detected',{}),{stage:'recovering',retry_stage:'manual_send'}));
test('ineligible deadline produces visible diagnostic rather than silent skip',()=>assert.deepEqual(webProgress('adapter.retry_unavailable',{reason:'total_deadline_expired'}),{stage:'retry_unavailable',retry_reason:'total_deadline_expired'}));
