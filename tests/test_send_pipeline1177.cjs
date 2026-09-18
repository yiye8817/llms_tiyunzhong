'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const { createRequire } = require('node:module');
const { EventEmitter } = require('node:events');
const { inputChunks, promptMetrics, requestMetrics } = require('../electron/input-transport.cjs');

for (const [name, text, size] of [
  ['short', '你好', 4096], ['large', 'a'.repeat(80001), 4096],
  ['multilingual', '😀汉字e\u0301\n'.repeat(2000), 257],
  ['pair_boundary', 'a'.repeat(255) + '😀' + 'b'.repeat(700), 256],
  ['line_endings', 'line\r\nnext\r\n'.repeat(200), 256],
]) test(`input chunks are lossless: ${name}`, () => {
  const chunks = inputChunks(text, size);
  assert.equal(chunks.join(''), text.replace(/\r\n?/g, '\n'));
  assert.ok(chunks.every(x => x.length <= size && !/^[\uDC00-\uDFFF]|[\uD800-\uDBFF]$/.test(x)));
  assert.equal(promptMetrics(text).prompt_sha256, promptMetrics(chunks.join('')).prompt_sha256);
});
test('invalid chunk configuration never loops or accepts unbounded sizes', () => {
  for (const size of [0, -1, NaN, 3.4, '4096', 20000]) assert.throws(() => inputChunks('x', size));
});
test('diagnostics compare the actual JSON body but never disclose content or secrets', () => {
  const prompt = '完整任务 TOKEN_PRIVATE';
  const report = requestMetrics({postData: JSON.stringify({messages:[{role:'user',content:prompt}], token:'PRIVATE'})},prompt);
  assert.equal(report.prompt_match, 'exact'); assert.ok(report.body_utf8_bytes > prompt.length);
  assert.doesNotMatch(JSON.stringify(report), /PRIVATE|完整任务|messages|token/i);
});
test('missing, invalid and different HTTP bodies remain unverified, never assumed equivalent', () => {
  assert.equal(requestMetrics({}, 'x').prompt_match, 'unavailable');
  for (const postData of ['invalid', '{"content":"different"}', 'x'.repeat(4_000_001)])
    assert.equal(requestMetrics({postData}, 'x').prompt_match, 'unverified');
});

function adapterClass() {
  const file = path.join(__dirname,'../electron/adapter.cjs'), local = createRequire(file), module = {exports:{}};
  class Plain { use() {} remove() {} addRule() {} turndown(s) {return s.replace(/<[^>]+>/g,'');} }
  vm.runInNewContext(fs.readFileSync(file,'utf8'), {module,exports:module.exports,require:id=>id==='turndown'?Plain:id==='turndown-plugin-gfm'?{gfm(){}}:local(id),URL,Date,setTimeout,clearTimeout,console}, {filename:file});
  return module.exports.WebsiteAdapter;
}
function fixture(options={}) {
  const id = options.provider || 'qwen', url=id==='glm'?'https://chat.z.ai/':'https://chat.qwen.ai/';
  let visible=false, focus=false, draft='', sent=0, loads=0, releases=0, attached=false, answer='', echo=false;
  const debug=new EventEmitter(), wc=new EventEmitter(), logs=[], commands=[], actions=[];
  const page=()=>({visibilityState:visible?'visible':'hidden',hasFocus:focus});
  const session=()=>({inputReady:visible,inputPresent:visible,inputEmpty:!draft,answers:0,userCount:0,stopping:false,model:null,rating:[]});
  const report=()=>({page:page(),summary:{},inputReady:visible,inputEmpty:!draft,answers:answer?[{text:answer,html:`<p>${answer}</p>`}]:[],stopping:false,userCount:echo?1:0,lastUserMatchesPrompt:echo});
  function done() {
    if(options.requireVisibleCommit && !visible) return;
    draft=''; echo=true; answer='完整回答';
    debug.emit('message',{},'Network.requestWillBeSent',{requestId:'n',type:'Fetch',request:{method:'POST',url:url+'api/chat/completions'}});
    debug.emit('message',{},'Network.responseReceived',{requestId:'n',type:'Fetch',response:{url:url+'api/chat/completions',status:200}});
    debug.emit('message',{},'Network.loadingFinished',{requestId:'n'});
  }
  Object.assign(debug,{
    isAttached:()=>attached,attach:()=>{attached=true;},detach:()=>{attached=false;},
    async sendCommand(method,params={}) {
      commands.push({method,params});
      if(method==='Emulation.setFocusEmulationEnabled')focus=params.enabled;
      if(method==='Input.insertText') {assert.ok(visible&&focus);draft+=params.text;options.onChunk?.({draft,commands});}
      if(method==='Input.dispatchMouseEvent'&&params.type==='mouseReleased') {
        assert.ok(visible&&focus);sent++;options.onSend?.(draft);
        if(!options.ignoreClick) {
          if(options.delay) {echo=true;setTimeout(done,options.delay);} else done();
        }
      }
    },
  });
  Object.assign(wc,{debugger:debug,isDestroyed:()=>false,getURL:()=>url,stop(){},async loadURL(){loads++;},
    async executeJavaScriptInIsolatedWorld(_id,sources) {
      const code=sources[0].code;
      if(code.includes("})('abort',"))return true;
      const tail=code.match(/\}\)\(("(?:[^"\\]|\\.)*"),(\{[^\n]*\})\); \}\)\(\)$/);
      assert.ok(tail);
      const a=JSON.parse(tail[1]),args=JSON.parse(tail[2]);actions.push(a);
      if(a==='inspect')return report();
      if(a===id+'Session')return session();
      if(a==='prepare')return {ready:!draft,inputMethod:'cdp_insert_text',inputFocused:focus,page:page()};
      if(a==='inputProgress')return {ready:!options.corrupt&&draft===args.prompt.replace(/\r\n?/g,'\n').slice(0,args.input_offset),inputLength:draft.length,reason:options.corrupt?'prefix_changed':'prefix_verified'};
      if(a==='canSubmit'||a==='claimSubmit')return {ready:draft===args.prompt.replace(/\r\n?/g,'\n'),method:'button',page:page(),x:10,y:10,inputLength:draft.length,expectedLength:args.prompt.length};
      if(a==='recoveryInspect')return {currentError:false,contextChanged:false};
      throw new Error('unexpected action '+a);
    }});
  const adapter=new (adapterClass())(wc,{id,url,selectors:{input:['textarea'],send:['#send'],assistant:['.answer'],stop:[]}},()=>{},(event,fields)=>logs.push({event,...fields}),{
    async acquireInput() {visible=true;return ()=>{visible=false;releases++;};},
  });
  return {adapter,logs,commands,actions,get sent(){return sent;},get loads(){return loads;},get releases(){return releases;}};
}
const job=extra=>({job_id:'test1177',purpose:'candidate',prompt:'当前任务',timeout_seconds:8,submission_timeout_seconds:.8,recovery_timeout_seconds:0,stable_seconds:.01,min_wait_seconds:0,...extra});
async function finish(t,promise) {
  let result;promise.then(value=>{result={value};},error=>{result={error};});
  for(let elapsed=0;!result&&elapsed<12000;elapsed+=100){await new Promise(setImmediate);t.mock.timers.tick(100);}
  assert.ok(result,'deadline is bounded');if(result.error)throw result.error;return result.value;
}
function clock(t){t.mock.timers.enable({apis:['setTimeout','Date'],now:Date.now()});t.after(()=>t.mock.timers.reset());}
for(const provider of ['glm','qwen']) test(`${provider}: hidden composer is revealed before first readiness check`,async t=>{
  clock(t);const s=fixture({provider});assert.equal(await finish(t,s.adapter.run(job())), '完整回答');
  assert.ok(s.logs.findIndex(x=>x.event==='adapter.input_lease_acquired')<s.logs.findIndex(x=>x.event==='adapter.input_prepared'));
  assert.equal(s.sent,1);assert.equal(s.loads,0);assert.equal(s.releases,1);
});
test('long Agent prompt uses verified native chunks and one final submission',async t=>{
  clock(t);const prompt=('工具 schema 与系统规则😀\n'+JSON.stringify({a:'"quoted"',b:'\\n'})).repeat(1900);
  const s=fixture({onSend:actual=>assert.equal(actual,prompt)});
  assert.equal(await finish(t,s.adapter.run(job({prompt,input_chunk_delay_ms:0,timeout_seconds:10}))), '完整回答');
  const chunks=s.commands.filter(x=>x.method==='Input.insertText');assert.ok(chunks.length>10);
  assert.equal(chunks.map(x=>x.params.text).join(''),prompt);assert.equal(s.sent,1);
  assert.equal(s.actions.filter(x=>x==='inputProgress').length,2*chunks.length);
});
test('prefix mismatch aborts without sending, overwriting or repeating native input',async t=>{
  clock(t);const s=fixture({corrupt:true});await assert.rejects(finish(t,s.adapter.run(job({prompt:'x'.repeat(12000)}))), e=>e.code==='input_prefix_mismatch');
  assert.equal(s.sent,0);assert.equal(s.commands.filter(x=>x.method==='Input.insertText').length,0);assert.equal(s.releases,1);
});
test('Qwen deferred submit commit keeps visibility briefly instead of cancelling it immediately',async t=>{
  clock(t);const s=fixture({delay:600,requireVisibleCommit:true});
  assert.equal(await finish(t,s.adapter.run(job())), '完整回答');assert.equal(s.sent,1);
  assert.equal(s.logs.find(x=>x.event==='adapter.submit_settle_finished').evidence,'generation_request_started');
});
test('ignored clicks release the input lease at the bounded settle limit and never resend',async t=>{
  clock(t);const s=fixture({ignoreClick:true});
  await assert.rejects(finish(t,s.adapter.run(job({submit_settle_seconds:.3}))),e=>e.code==='submission_unconfirmed');
  assert.equal(s.sent,1);assert.equal(s.releases,1);
  assert.equal(s.logs.find(x=>x.event==='adapter.submit_settle_finished').evidence,'bounded_settle_elapsed');
});
test('common candidate deadline bounds even the post-click commit wait',async t=>{
  clock(t);const s=fixture({ignoreClick:true});
  await assert.rejects(finish(t,s.adapter.run(job({submit_settle_seconds:10,total_timeout_seconds:2}))),e=>['timeout','submission_unconfirmed'].includes(e.code));
  assert.equal(s.sent,1);assert.equal(s.releases,1);
});
