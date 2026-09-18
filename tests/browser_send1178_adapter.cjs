'use strict';
// Production adapter + actual Chromium CDP/DOM. Host leases and server events
// are deterministic fixtures; this does not claim live provider verification.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module'),{spawn}=require('node:child_process'),{createInterface}=require('node:readline'),{EventEmitter}=require('node:events');
const assert=require('node:assert/strict');
const file=path.join(process.env.FUSION_TEST_SOURCE_ROOT || path.join(__dirname,'..'),'electron/adapter.cjs'),local=createRequire(file),moduleObject={exports:{}};
class Plain {use(){}remove(){}addRule(){}turndown(s){return s.replace(/<[^>]+>/g,'');}}
vm.runInNewContext(fs.readFileSync(file,'utf8'),{module:moduleObject,exports:moduleObject.exports,require:id=>id==='turndown'?Plain:id==='turndown-plugin-gfm'?{gfm(){}}:local(id),URL,Date,setTimeout,clearTimeout,console},{filename:file});
const worker=spawn(process.env.PYTHON||'python',['-u',path.join(__dirname,'browser_send1178_worker.py')],{stdio:['pipe','pipe','inherit']});
const pending=new Map(),sites=new Map();let seq=0;
const failAll=e=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(e);}pending.clear();};
worker.on('error',failAll);worker.on('exit',code=>failAll(new Error(`worker exited ${code}`)));
createInterface({input:worker.stdout}).on('line',line=>{
 const p=JSON.parse(line);if(p.event){const site=sites.get(p.provider);if(p.event==='route')site.url=p.params.url;else site?.debug.emit('message',{},p.event,p.params);return;}
 const task=pending.get(p.id);if(!task)return;pending.delete(p.id);clearTimeout(task.timer);if(p.error)task.reject(new Error(p.error));else task.resolve(p.result);
});
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++seq,timer=setTimeout(()=>{pending.delete(id);reject(new Error(`worker timeout ${method}`));},15000);pending.set(id,{resolve,reject,timer});worker.stdin.write(JSON.stringify({id,method,params})+'\n');});}
(async()=>{
 const rows=[];let owner=null;const queue=[];let version='';
 try {
  version=(await call('start')).version;
  const adapters={};
  for(const [id,url] of [['qwen','https://chat.qwen.ai/'],['glm','https://chat.z.ai/']]) {
   const debug=new EventEmitter(),site={url,debug,commands:[],logs:[],sentAt:null,completeAt:null};sites.set(id,site);let attached=false;
   Object.assign(debug,{isAttached:()=>attached,attach:()=>{attached=true;},detach:()=>{attached=false;},sendCommand:async(method,params={})=>{
    site.commands.push({method,params});if(method.startsWith('Input.'))assert.equal(owner,id);return call('cdp',{provider:id,method,params});}});
   const wc=new EventEmitter();Object.assign(wc,{debugger:debug,isDestroyed:()=>false,getURL:()=>site.url,stop(){},async loadURL(){throw new Error('must not reload');},executeJavaScriptInIsolatedWorld:(_i,sources)=>call('evaluate',{provider:id,code:sources[0].code})});
   adapters[id]=new moduleObject.exports.WebsiteAdapter(wc,{id,url,selectors:{input:['.stale-input'],send:['.stale-send'],assistant:['[data-role="assistant"]'],stop:[]}},()=>{},(event,fields)=>{
    site.logs.push({event,...fields});if(event==='adapter.submission_dispatched')site.sentAt=Date.now();if(event==='adapter.complete')site.completeAt=Date.now();
   },{async acquireInput(){if(owner)await new Promise(resolve=>queue.push(resolve));assert.equal(owner,null);owner=id;await call('lease',{provider:id});return async()=>{assert.equal(owner,id);await call('release',{provider:id});owner=null;queue.shift()?.();};}});
  }
  const prompt=('工具 schema 和原始任务😀\n'+JSON.stringify({tool:'python.run',arguments:{code:'print("ok")\n'}})+'\n').repeat(700);
  const results=await Promise.all(Object.entries(adapters).map(async([id,adapter])=>{
   const text=id==='qwen'?prompt:'GLM 第一段\n第二段：保留所选模型。';
   const result=await adapter.run({job_id:id,purpose:'candidate',prompt:text,timeout_seconds:20,total_timeout_seconds:20,submission_timeout_seconds:5,recovery_timeout_seconds:10,stable_seconds:.05,min_wait_seconds:0,input_chunk_delay_ms:10});
   assert.equal(result,id+' complete');const stats=await call('stats',{provider:id});assert.equal(stats.sends,1);assert.equal(stats.requests,id==='qwen'?2:1);assert.equal(stats.retries,id==='qwen'?1:0);assert.equal(stats.blockedCommits,0);assert.equal(stats.sentText,text);
   const site=sites.get(id);if(id==='qwen')assert.equal(site.logs.find(x=>x.event==='adapter.submit_settle_finished'&&x.operation==='dom_handler')?.evidence,'generation_request_started');const edits=site.commands.filter(x=>x.method==='Input.insertText');assert.ok(stats.edits >= edits.length, "one native multiline insertion may emit multiple input events");
   assert.equal(site.logs.find(x=>x.event==='adapter.request_payload').prompt_match,'exact');
   rows.push({name:id==='qwen'?'qwen_long_prompt_deferred_retry_commits_once':'glm_button_mounts_only_after_native_input',ok:true,chars:text.length,edits:edits.length,sends:stats.sends,requests:stats.requests,retries:stats.retries});return result;
  }));
  assert.equal(results.length,2);assert.equal(owner,null);
  assert.ok(sites.get('glm').sentAt<sites.get('qwen').completeAt,'GLM must send while Qwen response is still pending');
  rows.push({name:'dual_model_sends_overlap_without_waiting_for_first_answer',ok:true});
 }catch(e){rows.push({name:'adapter_integration',ok:false,error:e.stack,details:e.details,providers:Object.fromEntries([...sites].map(([id,s])=>[id,{logs:s.logs.filter(x=>!['adapter.start','adapter.dom','adapter.cdp_started'].includes(x.event)).slice(-30)}]))});process.exitCode=1;}
 finally{await call('close').catch(()=>{});worker.stdin.end();setTimeout(()=>{if(worker.exitCode===null)worker.kill();},1000).unref();}
 const report={browser:version,total:rows.length,passed:rows.filter(x=>x.ok).length,scope:'production adapter + Chromium; fixture host, routes and Network events; no live accounts',cases:rows};
 fs.writeFileSync(process.argv[2]||'/tmp/send1178-adapter.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
})();
