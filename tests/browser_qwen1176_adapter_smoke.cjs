'use strict';
// Optional integration test: production adapter and pageAction execute against
// real Chromium via a local Playwright worker. Only HTML-to-Markdown conversion
// is replaced when npm packages are unavailable. Routes and network events are fixtures; no navigation or HTTP requests.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { EventEmitter } = require('node:events');
const assert = require('node:assert/strict');
const root = process.env.FUSION_TEST_SOURCE_ROOT || path.join(__dirname, '..');
const file = path.join(root, 'electron/adapter.cjs');
const localRequire = createRequire(file), mod = { exports: {} };
class PlainText { use() {} remove() {} addRule() {} turndown(html) { return html.replace(/<[^>]*>/g, ''); } }
const requireStub = id => id === 'turndown' ? PlainText : id === 'turndown-plugin-gfm' ? { gfm() {} } : localRequire(id);
vm.runInNewContext(fs.readFileSync(file, 'utf8'), { module: mod, exports: mod.exports, require: requireStub, URL, Date, setTimeout, clearTimeout, console }, { filename:file });
const { WebsiteAdapter } = mod.exports;
const worker = spawn(process.env.PYTHON || 'python', ['-u', path.join(__dirname, 'browser_qwen1176_worker.py')], { stdio:['pipe','pipe','inherit'] });
const pending = new Map(); let number = 0, debug = null, url = '';
const rejectAll = error => { for (const { reject, timer } of pending.values()) { clearTimeout(timer); reject(error); } pending.clear(); };
worker.on('error', rejectAll); worker.on('exit', code => rejectAll(new Error(`browser worker exited: ${code}`)));
createInterface({ input:worker.stdout }).on('line', line => {
  let packet; try { packet=JSON.parse(line); } catch { return; }
  if (packet.event) {
    if(packet.event==='Page.navigatedWithinDocument')url=packet.params.url;
    debug?.emit('message', {}, packet.event, packet.params);return;
  }
  const task=pending.get(packet.id);if(!task)return;pending.delete(packet.id);clearTimeout(task.timer);
  if(packet.error)task.reject(new Error(packet.error));else task.resolve(packet.result);
});
function call(method, params = {}) {
  return new Promise((resolve,reject)=>{
    const id=++number;
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`worker command timed out: ${method}`));},15000);
    pending.set(id,{resolve,reject,timer});worker.stdin.write(JSON.stringify({id,method,params})+'\n');
  });
}
(async()=>{
 const rows=[];let version='';
 try{
  for(const item of [
   {name:'network_error_HTTP200', options:{}, clicks:1},
   {name:'network_error_delayed_after_503',options:{firstStatus:503,errorDelay:1000},clicks:1},
   {name:'retry_button_delayed',options:{buttonDelay:1200},clicks:1},
   {name:'manual_recovery_before_button',options:{buttonDelay:2000,manual:true},clicks:0},
   {name:'second_network_failure_never_reclicked',options:{alwaysFail:true},clicks:1,error:'recovery_timeout'},
  ]){
   debug=null;const start=await call('start',item.options);url=start.url;version=start.browser;
   debug=new EventEmitter();let attached=false,loads=0;const logs=[],commands=[],stages=[];
   Object.assign(debug,{isAttached:()=>attached,attach:()=>{attached=true;},detach:()=>{attached=false;},
     sendCommand:async(method,params={})=>{commands.push({method,params});return call('cdp',{method,params});}});
   const wc=new EventEmitter();Object.assign(wc,{debugger:debug,isDestroyed:()=>false,getURL:()=>url,
    async loadURL(){loads++;throw new Error('unexpected page reload');},stop(){},
    executeJavaScriptInIsolatedWorld:(_world,sources)=>call('evaluate',{code:sources[0].code})});
   const adapter=new WebsiteAdapter(wc,{id:'qwen',url:start.url,selectors:{input:['#chat-input'],send:['#send'],assistant:['[data-role="assistant"]'],stop:[],new_chat:[]}},
    ()=>{},(event,fields)=>logs.push({event,...fields}));
   const started=Date.now();let result,error;
   try{result=await adapter.run({job_id:item.name,request_id:item.name,purpose:'candidate',prompt:'本地 Chromium 重试验证',timeout_seconds:8,
     recovery_timeout_seconds:item.error?3:7,submission_timeout_seconds:1.5,total_timeout_seconds:12,stable_seconds:0.1,min_wait_seconds:0},undefined,stage=>stages.push(stage));}
   catch(e){error=e;}
   const stats=await call('stats');
   try{
    if(item.error)assert.equal(error?.code,item.error,error?.message);else{if(error)throw error;assert.equal(result,'Complete recovered fixture answer');}
    assert.equal(stats.sends,1);assert.equal(stats.retries,item.clicks);assert.equal(stats.requests,2);
    assert.equal(commands.filter(c=>c.method==='Input.insertText').length,1);assert.equal(loads,0);
    assert.equal(stages.filter(s=>s.stage==='retrying').length,item.clicks);
    rows.push({name:item.name,ok:true,stats,elapsed_ms:Date.now()-started,completion:error?.code||'complete',automatic_clicks:item.clicks});
   }catch(e){rows.push({name:item.name,ok:false,error:String(e),adapter_error:error?.code,stats,logs:logs.filter(l=>!['adapter.dom','adapter.start'].includes(l.event))});}
  }
 }finally{try{await call('close');}catch{}worker.stdin.end();worker.kill();}
 const report={chromium_version:version,scope:'Production adapter + DOM + real CDP input; route/network-event fixtures and text converter stub; no HTTP, Qwen account or Electron',
  total:rows.length,passed:rows.filter(r=>r.ok).length,failed:rows.filter(r=>!r.ok).length,tests:rows};
 const output=process.argv[2];if(output)fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify(report,null,2));if(report.failed)process.exitCode=1;
})().catch(error=>{console.error(error);worker.kill();process.exitCode=1;});
