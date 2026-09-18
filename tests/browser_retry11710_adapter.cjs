'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),os=require('node:os');
const {createRequire}=require('node:module'),{spawn}=require('node:child_process'),{createInterface}=require('node:readline'),{EventEmitter}=require('node:events');
const assert=require('node:assert/strict'),{BitmapImage}=require('./native_image_fixture.cjs');
const root=process.env.FUSION_TEST_SOURCE_ROOT || path.join(__dirname,'..'),file=path.join(root,'electron/adapter.cjs'),local=createRequire(file),mod={exports:{}};
class Plain{use(){}remove(){}addRule(){}turndown(s){return s.replace(/<[^>]+>/g,'');}}
vm.runInNewContext(fs.readFileSync(file,'utf8'),{module:mod,exports:mod.exports,require:id=>id==='turndown'?Plain:id==='turndown-plugin-gfm'?{gfm(){}}:local(id),URL,Date,setTimeout,clearTimeout,console},{filename:file});
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'qwen1179-'));
process.env.FUSION_RETRY_DIR=path.join(temp,'attempts');process.env.FUSION_RETRY_LEARNING_DIR=path.join(temp,'learning');
async function scenario(mode,human=false,wait=2){
 const worker=spawn(process.env.PYTHON||'python',['-u',path.join(__dirname,'browser_retry11710_worker.py')],{stdio:['pipe','pipe','inherit']});
 const pending=new Map();let seq=0,url='https://chat.qwen.ai/',attached=false,owner=false,humanSent=false;
 const logs=[],commands=[],bodies=new Map(),debug=new EventEmitter();
 createInterface({input:worker.stdout}).on('line',line=>{
  const p=JSON.parse(line);if(p.event){if(p.event==='route')url=p.params.url;else if(p.event==='fixtureBody')bodies.set(p.params.requestId,p.params.body);else debug.emit('message',{},p.event,p.params);return;}
  const t=pending.get(p.id);if(t){clearTimeout(t.timer);pending.delete(p.id);p.error?t.reject(new Error(p.error)):t.resolve(p.result);}
 });
 function call(method,params={}){return new Promise((resolve,reject)=>{const id=++seq,timer=setTimeout(()=>{pending.delete(id);reject(new Error('worker timeout '+method));},15000);pending.set(id,{resolve,reject,timer});worker.stdin.write(JSON.stringify({id,method,params})+'\n');});}
 worker.on('exit',()=>{for(const t of pending.values()){clearTimeout(t.timer);t.reject(new Error('worker closed'));}pending.clear();});
 Object.assign(debug,{isAttached:()=>attached,attach(){attached=true;},detach(){attached=false;},sendCommand:async(method,params={})=>{
  commands.push({method,params});if(method==='Network.getResponseBody')return {body:bodies.get(params.requestId)||'',base64Encoded:false};if(method.startsWith('Input.'))assert.ok(owner,'native input requires current input lease');return call('cdp',{method,params});}});
 const wc=new EventEmitter();Object.assign(wc,{debugger:debug,isDestroyed:()=>false,getURL:()=>url,stop(){},async loadURL(){throw new Error('reload forbidden');},capturePage:async()=>BitmapImage.from(await call('screenshot')),executeJavaScriptInIsolatedWorld:(_i,sources)=>call('evaluate',{code:sources[0].code})});
 const adapter=new mod.exports.WebsiteAdapter(wc,{id:'qwen',url,selectors:{input:['textarea'],send:['#send-message-button'],assistant:['[data-role="assistant"]'],stop:[]}},(state)=>{
  if(state==='manual_retry_required'&&human&&!humanSent){humanSent=true;setTimeout(()=>call('human').catch(()=>{}),100);}
 },(event,fields)=>logs.push({event,...fields}),{async acquireInput(){assert.equal(owner,false);owner=true;await call('lease');return async()=>{await call('release');owner=false;};}});
 let result,err;
 try{
  const {version}=await call('start',{mode});
  try{result=await adapter.run({job_id:'test-'+mode,purpose:'candidate',prompt:'实际输入：本轮任务\n请回答。',timeout_seconds:15,total_timeout_seconds:12,submission_timeout_seconds:5,recovery_timeout_seconds:10,stable_seconds:.05,min_wait_seconds:0,qwen_retry_trigger_wait_seconds:.5,qwen_manual_retry_wait_seconds:wait});}catch(e){err=e;}
  const stats=await call('stats');assert.equal(stats.sends,1);assert.equal(stats.sentText,'实际输入：本轮任务\n请回答。');
  if(mode==='never'){
   assert.equal(err?.code,'qwen_manual_retry_timeout');assert.equal(stats.requests,1);
   assert.ok(logs.some(l=>l.event==='adapter.retry_manual_wait'&&l.remaining_seconds===1));
  }else{
   if(err)throw err;assert.equal(result,'qwen complete');assert.equal(stats.requests,2);assert.equal(stats.blockedCommits,0);
   if(['native','icon-native'].includes(mode)){
    assert.deepEqual(stats.trusted,[false,true]);assert.ok(logs.some(l=>l.event==='adapter.retry_visual_match'&&l.reason==='screenshot_semantic_target'));
   }
   if(['manual','stale-stop','plain-card'].includes(mode))assert.deepEqual(stats.trusted,[false,true,true]);
   if(human&&mode!=='payload-error'){assert.ok(logs.some(l=>l.event==='adapter.retry_manual_click'));assert.ok(logs.some(l=>l.event==='adapter.retry_learning_saved'));}
   if(mode==='learned'){assert.ok(logs.some(l=>l.event==='adapter.retry_visual_match'&&l.reason==='screenshot_learned_template'));assert.equal(humanSent,false);}
  }
  if(['stale-stop','plain-card'].includes(mode))assert.deepEqual(logs.filter(l=>l.event==='adapter.retry_stage_started').map(l=>l.stage),['dom_handler','screenshot_click','manual']);
  if(mode==='payload-error') {assert.ok(logs.some(l=>l.event==='adapter.network_application_error'));assert.equal(stats.retries,1);assert.equal(logs.some(l=>l.event==='adapter.retry_learning_saved'),false);}
  assert.equal(owner,false);
  assert.ok(logs.some(l=>l.event==='adapter.retry_watch_stopped'));
  return {name:mode,ok:true,browser:version,stats,stages:logs.filter(l=>['adapter.retry_stage_started','adapter.retry_stage_result','adapter.retry_visual_match','adapter.retry_finished','adapter.retry_learning_saved'].includes(l.event)).map(({candidates,...l})=>l)};
 }catch(e){return {name:mode,ok:false,error:e.stack,details:e.details,logs:logs.filter(l=>!['adapter.start','adapter.dom'].includes(l.event)).slice(-45)};}
 finally{await call('close').catch(()=>{});worker.stdin.end();setTimeout(()=>worker.kill(),1000).unref();}
}
(async()=>{
 const rows=[];
 for(const [mode,human,wait] of [['stale-stop',true,3],['plain-card',true,3],['payload-error',true,3],['icon-native',false,2]]){
  if(process.env.FUSION_TEST_CASE && mode!==process.env.FUSION_TEST_CASE)continue;
  if(mode!=='learned')fs.rmSync(process.env.FUSION_RETRY_LEARNING_DIR,{recursive:true,force:true});
  const row=await scenario(mode,human,wait);rows.push(row);console.log(mode,row.ok?'PASS':'FAIL');
 }
 const report={scope:'production adapter and real Chromium pixels/CDP; synthetic page/server and simulated human input; no live accounts',total:rows.length,passed:rows.filter(r=>r.ok).length,artifacts:temp,cases:rows};
 const output=process.argv[2]||'/tmp/retry1179-browser.json';fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({total:report.total,passed:report.passed,output,artifacts:temp}));process.exitCode=report.passed===report.total?0:1;
})();
