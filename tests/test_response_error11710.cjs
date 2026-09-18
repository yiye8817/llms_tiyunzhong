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
 const debug=new EventEmitter(),logs=[],bodies=new Map();debug.sendCommand=async(method,p)=>method==='Network.getResponseBody'?{body:bodies.get(p.requestId)||'',base64Encoded:false}:{};
 const stop=await startNetworkTrace({debugger:debug},(event,fields)=>logs.push({event,...fields}),Date.now()+2000,undefined,{prompt,origin,provider:'qwen'});
 t.after(()=>stop());stop.arm();
 const start=(id,url=generation,postData=body(),extra={})=>debug.emit('message',{},'Network.requestWillBeSent',{requestId:id,type:'Fetch',...extra,request:{method:'POST',url,...(postData===null?{}:{postData}),...extra.request}});
 const response=(id,url=generation,status=503,mimeType='application/json')=>debug.emit('message',{},'Network.responseReceived',{requestId:id,type:'Fetch',response:{url,status,mimeType}});
 const end=id=>debug.emit('message',{},'Network.loadingFinished',{requestId:id,encodedDataLength:1024});
 const fail=id=>debug.emit('message',{},'Network.loadingFailed',{requestId:id,errorText:'net::ERR_CONNECTION_RESET'});
 return {stop,start,response,end,fail,logs,bodies};
}

const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
test('HTTP 200 error body triggers failure after finished and does not log its message',async t=>{
 const s=await fixture(t);s.bodies.set('one','{"error":{"code":502,"message":"SECRET"}}');s.start('one');s.response('one',generation,200);s.end('one');await flush();
 assert.equal(s.stop.state().pending,0);assert.equal(s.stop.state().failed[0].status,502);assert.equal(s.stop.terminalFailure(),true);assert.doesNotMatch(JSON.stringify(s.logs),/SECRET/);
});
test('normal generation can quote a 502 error without starting recovery',async t=>{
 const s=await fixture(t);s.bodies.set('one',JSON.stringify({choices:[{delta:{content:'502 network error'}}]}));s.start('one');s.response('one',generation,200);s.end('one');await flush();
 assert.equal(s.stop.state().failed.length,0);assert.equal(s.stop.terminalFailure(),false);
});
test('old failure cannot waive the busy check for a new successful attempt',async t=>{
 const s=await fixture(t);s.start('one');s.response('one',generation,502);s.end('one');assert.equal(s.stop.terminalFailure(),true);
 s.start('two');assert.equal(s.stop.terminalFailure(),false);s.response('two',generation,200);s.end('two');await flush();assert.equal(s.stop.terminalFailure(),false);
});
test('unrelated helper response body is never inspected',async t=>{
 const s=await fixture(t);s.bodies.set('helper','{"error":{"code":502}}');s.start('helper',generation,body('summarize a title'));s.response('helper',generation,200);s.end('helper');await flush();
 assert.equal(s.stop.state().failed.length,0);assert.equal(s.logs.some(r=>r.event==='adapter.network_application_error'),false);
});
