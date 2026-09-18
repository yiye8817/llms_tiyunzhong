"use strict";
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {createRequire} = require('node:module'), {EventEmitter} = require('node:events');
const root = process.env.FUSION_TEST_SOURCE_ROOT || path.join(__dirname,'..');
const file = path.join(root,'electron/adapter.cjs'), local = createRequire(file), m={exports:{}};
class Plain {use(){} remove(){} addRule(){} turndown(s){return s;}}
vm.runInNewContext(fs.readFileSync(file,'utf8'),{module:m,exports:m.exports,require:id=>id==='turndown'?Plain:id==='turndown-plugin-gfm'?{gfm(){}}:local(id),URL,Date,setTimeout,clearTimeout,console},{filename:file});
const {startNetworkTrace}=m.exports;
const origin='https://chat.qwen.ai', prompt='任务\n完整内容😀SECRET_BODY';
const generation=origin+'/api/v2/chat/completions';
const body=(text=prompt,extra={})=>JSON.stringify({model:'qwen',stream:true,messages:[{role:'user',content:text}],...extra});
async function fixture(t) {
 const debug=new EventEmitter(),logs=[];debug.sendCommand=async()=>{};
 const stop=await startNetworkTrace({debugger:debug},(event,fields)=>logs.push({event,...fields}),Date.now()+2000,undefined,{prompt,origin,provider:'qwen'});
 t.after(()=>stop());stop.arm();
 const start=(id,url=generation,postData=body(),extra={})=>debug.emit('message',{},'Network.requestWillBeSent',{requestId:id,type:'Fetch',...extra,request:{method:'POST',url,...(postData===null?{}:{postData}),...extra.request}});
 const response=(id,url=generation,status=503,mimeType='application/json')=>debug.emit('message',{},'Network.responseReceived',{requestId:id,type:'Fetch',response:{url,status,mimeType}});
 const end=id=>debug.emit('message',{},'Network.loadingFinished',{requestId:id});
 const fail=id=>debug.emit('message',{},'Network.loadingFailed',{requestId:id,errorText:'net::ERR_CONNECTION_RESET'});
 return {stop,start,response,end,fail,logs};
}
test('unrelated cross-origin POST /chat failure is diagnostic, not this generation',async t=>{
 const s=await fixture(t),u='https://telemetry.example/chat';s.start('telemetry',u,JSON.stringify({event:'send'}));s.response('telemetry',u,503);s.end('telemetry');
 assert.equal(s.stop.state().failed.length,0);assert.equal(s.stop.state().observed,false);
 assert.ok(s.logs.some(x=>x.event==='adapter.network_response'&&x.status===503&&!x.tracked_response));
});
test('chat history persistence containing the prompt must not become a generation',async t=>{
 const s=await fixture(t),u=origin+'/api/chats/123/messages';s.start('save',u,body(prompt,{stream:false}));s.response('save',u,500);s.end('save');
 assert.equal(s.stop.state().failed.length,0);assert.equal(s.stop.state().observed,false);
});
test('an independent helper completion with a different latest user does not fail current task',async t=>{
 const s=await fixture(t);s.start('helper',generation,body('generate a title'));s.response('helper');s.fail('helper');
 assert.equal(s.stop.state().failed.length,0);assert.equal(s.stop.state().pending,0);assert.equal(s.stop.state().observed,false);
});
test('an old matching user in history is not evidence for a new different question',async t=>{
 const s=await fixture(t);s.start('old',generation,body(prompt,{messages:[{role:'user',content:prompt},{role:'assistant',content:'old'},{role:'user',content:'other'}]}));s.response('old');
 assert.equal(s.stop.state().failed.length,0);assert.equal(s.stop.state().pending,0);
});
test('an assistant-ended save is not a generation even with the exact user in history',async t=>{
 const s=await fixture(t);s.start('save',generation,body(prompt,{messages:[{role:'user',content:prompt},{role:'assistant',content:'saved answer'}]}));s.response('save');
 assert.equal(s.stop.state().failed.length,0);assert.equal(s.stop.state().pending,0);
});
test('an opaque same-origin streaming request needs an exact latest user to be tracked',async t=>{
 const s=await fixture(t),u=origin+'/api/v9/inference/opaque';s.start('new',u);s.response('new',u,200,'text/event-stream');
 assert.equal(s.stop.state().pending,1);s.fail('new');assert.equal(s.stop.state().failed.length,1);
});
test('an opaque stream with only a matching metadata string stays diagnostic',async t=>{
 const s=await fixture(t),u=origin+'/events';s.start('noise',u,JSON.stringify({stream:true,metadata:prompt}));s.response('noise',u,200,'text/event-stream');s.fail('noise');
 assert.equal(s.stop.state().pending,0);assert.equal(s.stop.state().failed.length,0);
});
test('a real current generation failure survives loadingFinished',async t=>{
 const s=await fixture(t);s.start('real');s.response('real',generation,429);s.end('real');
 assert.equal(s.stop.state().failed[0].status,429);assert.equal(s.stop.state().observed,true);
});
test('a known same-origin endpoint remains tracked when CDP omits a large request body',async t=>{
 const s=await fixture(t);s.start('large',generation,null);s.response('large',generation,200);assert.equal(s.stop.state().pending,1);s.fail('large');assert.equal(s.stop.state().failed.length,1);
});
test('a cross-origin known API with the actual latest user is allowed without ignoring real failures',async t=>{
 const s=await fixture(t),u='https://api.provider.example/chat/completions';s.start('api',u);s.response('api',u,403);s.end('api');assert.equal(s.stop.state().failed[0].status,403);
});
test('a tracked redirect is a single attempt and remains tracked on its final opaque URL',async t=>{
 const s=await fixture(t);s.start('redirect');s.start('redirect','https://worker.provider.example/run',null,{redirectResponse:{status:307,url:generation}});
 assert.equal(s.stop.checkpoint().responseCount,1);assert.equal(s.stop.state().pending,1);
 s.response('redirect','https://worker.provider.example/run',502);s.end('redirect');assert.equal(s.stop.state().failed[0].status,502);
});
test('available but malformed body never makes a known current-origin failure disappear',async t=>{
 const s=await fixture(t);s.start('bad',generation,'not json');s.response('bad');assert.equal(s.stop.state().failed.length,1);
});
test('split typed text parts match only after exact concatenation',async t=>{
 const s=await fixture(t),u=origin+'/opaque';s.start('parts',u,body(prompt,{messages:[{role:'user',content:[{type:'text',text:'任务\n'},{type:'text',text:'完整内容😀SECRET_BODY'}]}]}));
 assert.equal(s.stop.state().pending,1);
 assert.doesNotMatch(JSON.stringify(s.logs),/SECRET_BODY|完整内容/);
});
test('network diagnostics never add cookies, headers, query tokens or plaintext prompt',async t=>{
 const s=await fixture(t);s.start('private',generation+'?token=SECRET_QUERY',body(),{request:{headers:{Cookie:'SECRET_COOKIE'}}});s.response('private',generation+'?token=SECRET_QUERY');
 assert.doesNotMatch(JSON.stringify(s.logs),/SECRET_BODY|SECRET_QUERY|SECRET_COOKIE|完整内容/);
});
