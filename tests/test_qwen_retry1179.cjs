'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {runQwenRecovery,RetryArtifacts,imageFeature,featureSimilarity,selectVisualTarget}=require('../electron/qwen-retry.cjs');
const {BitmapImage}=require('./native_image_fixture.cjs');
const descriptor={tag:'button',id:'retry',aria:'Retry',title:'',role:'',testid:'',label:'Retry',classes:[]};
const bounds={x:0,y:0,width:48,height:48};
function image(blank=false){const raw=Buffer.alloc(48*48*4,255);for(let y=0;y<48;y++)for(let x=0;x<48;x++){const i=(y*48+x)*4;const v=blank?255:(x>15&&x<30||y>15&&y<30?0:255);raw[i]=raw[i+1]=raw[i+2]=v;}return new BitmapImage(48,48,raw,Buffer.from('test-only-not-PNG'));}
function artifact(t,job={}){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'retry1179-unit-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const logs=[];const a=new RetryArtifacts({origin:'https://chat.qwen.ai/',directory:path.join(dir,'attempts'),learningDirectory:path.join(dir,'learn')},{job_id:'job',...job},(event,fields)=>logs.push({event,...fields}));return{a,dir,logs};}
const click={trusted:true,origin:'https://chat.qwen.ai',descriptor,bounds,viewport:{width:48,height:48}};
test('blank image never grants a screenshot click',()=>{assert.equal(imageFeature(image(true)),null);assert.equal(selectVisualTarget(image(true),{candidates:[{semantic:true,bounds}]},null).target,null);});
test('image feature is normalized and identical templates match',()=>{const f=imageFeature(image());assert.equal(f.values.length,576);assert.equal(featureSimilarity(f,f),1);});
test('offscreen and oversized image regions are rejected',()=>{assert.equal(imageFeature(image(),{...bounds,x:-1}),null);assert.equal(imageFeature(image(),{...bounds,width:100}),null);});
test('one visible semantic retry is selected',()=>{assert.ok(selectVisualTarget(image(),{candidates:[{semantic:true,bounds}]},null).target);});
test('multiple equally plausible retry candidates are not guessed',()=>{assert.equal(selectVisualTarget(image(),{candidates:[{semantic:true,bounds},{semantic:true,bounds}]},null).target,null);});
test('anonymous icon does not become clickable just because it is unique',()=>{assert.equal(selectVisualTarget(image(),{candidates:[{icon:true,bounds}]},null).target,null);});
test('learned template recovers unlabelled current-turn control',()=>{const found=selectVisualTarget(image(),{candidates:[{learned:true,bounds}]},{feature:imageFeature(image())});assert.equal(found.reason,'screenshot_learned_template');});
test('corrupt learned pixels are rejected',()=>{const a=imageFeature(image());assert.equal(featureSimilarity({...a,values:['oops',...a.values.slice(1)]},a),0);});
test('attempt log is private JSONL with each attempt',t=>{const{a}=artifact(t);a.event('attempt',{stage:'one'});a.event('attempt',{stage:'two'});const f=path.join(a.folder,'attempts.jsonl');assert.equal(fs.statSync(f).mode&0o777,0o600);const events=fs.readFileSync(f,'utf8').trim().split('\n').map(JSON.parse);assert.deepEqual(events.filter(e=>e.event==='attempt').map(e=>e.stage),['one','two']);});
test('only trusted same-origin clicks may be promoted',t=>{const{a}=artifact(t);assert.equal(a.promote({...click,trusted:false},image()),false);assert.equal(a.promote({...click,origin:'https://other.test'},image()),false);assert.equal(a.load(),null);});
test('successful promotion roundtrips privately and stores evidence',t=>{const{a}=artifact(t);assert.equal(a.promote(click,image()),true);const row=a.load();assert.equal(row.evidence,'trusted_click_then_new_generation_and_complete_answer');assert.equal(row.feature.values.length,576);assert.equal(fs.statSync(a.learnFile).mode&0o777,0o600);assert.equal(row.confirmed,true);});
test('learning disabled prevents saving and loading',t=>{const{a}=artifact(t,{qwen_retry_learning:false});assert.equal(a.promote(click,image()),false);assert.equal(a.load(),null);});
test('mismatched viewport saves descriptor but not wrong pixel template',t=>{const{a}=artifact(t);assert.ok(a.promote({...click,viewport:{width:100,height:100}},image()));assert.equal(a.load().feature,null);});
test('learning file symlink does not redirect reads',t=>{const{a,dir}=artifact(t);fs.mkdirSync(path.dirname(a.learnFile));const other=path.join(dir,'outside');fs.writeFileSync(other,'{}');fs.symlinkSync(other,a.learnFile);assert.equal(a.load(),null);});
test('content logging disabled omits screenshots, not attempt metadata',t=>{const old=process.env.FUSION_LOG_CONTENT;process.env.FUSION_LOG_CONTENT='0';t.after(()=>old===undefined?delete process.env.FUSION_LOG_CONTENT:process.env.FUSION_LOG_CONTENT=old);const{a}=artifact(t);assert.equal(a.saveImage('test',image()),null);assert.ok(fs.existsSync(path.join(a.folder,'attempts.jsonl')));});
function simulation(t,{completeStage=null,defaultManual=true,deadline=40000,pending=false}={}){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'retry1179-sim-'));t.after(()=>fs.rmSync(temp,{recursive:true,force:true}));
 let now=1000,requests=1,failures=1,done=false,leased=false,resumed=false;const actions=[],logs=[],statuses=[];const originalNow=Date.now;Date.now=()=>now;t.after(()=>Date.now=originalNow);
 const network={checkpoint:()=>({responseCount:requests,failureCount:failures}),state:()=>({observed:true,pending:pending?1:0,failed:done?[]:[{status:502}]}),acknowledgeFailures(){}};
 const snapshot=()=>({stopping:false,responsePending:pending,recovery:{contextValid:true,currentTurnAccepted:true,currentError:!done}});
 const ctx={job:{job_id:'sim',qwen_retry_trigger_wait_seconds:.5,...(defaultManual?{}:{qwen_manual_retry_wait_seconds:1})},origin:'https://chat.qwen.ai',artifacts:{directory:path.join(temp,'logs'),learningDirectory:path.join(temp,'learn')},cause:{code:'response_http_error'},deadline,network,
  log:(event,fields)=>logs.push({event,...fields}),status:(state,message)=>statuses.push({state,message}),wc:{},
  fail:(code,message)=>Object.assign(new Error(message),{code}),bounded:async p=>p,signal:null,
  pause:async ms=>{now+=ms;},observe:async()=>snapshot(),acquire:async()=>{assert.equal(leased,false);leased=true;},release:async()=>{leased=false;},
  act:async(name,args)=>{actions.push(name);if(name==='recoveryTrigger'&&completeStage==='dom_handler'){done=true;requests++;return{dispatched:true};}
    if(name==='recoveryWatchPoll')return{clicks:[]};return {ready:false,reason:'fixture_missing',clicks:[]};},
  dispatch:async()=>{throw new Error('unexpected native input');},resume:()=>resumed=true,
  pollComplete:()=>({done:done&&resumed,value:'complete answer'})};
 return {ctx,logs,actions,statuses,get now(){return now;},get leased(){return leased;}};
}
test('default manual stage really waits 20 seconds without another request',async t=>{const s=simulation(t);await assert.rejects(runQwenRecovery(s.ctx),e=>e.code==='qwen_manual_retry_timeout');const waits=s.logs.filter(l=>l.event==='adapter.retry_manual_wait');assert.equal(waits[0].remaining_seconds,20);assert.equal(waits.at(-1).remaining_seconds,0);assert.ok(s.now>=21000);assert.deepEqual(s.logs.filter(l=>l.event==='adapter.retry_stage_started').map(l=>l.stage),['dom_handler','screenshot_click','manual']);assert.equal(s.actions.filter(a=>a==='recoveryTrigger').length,1);assert.equal(s.leased,false);});
test('remaining job deadline is never silently extended to provide manual time',async t=>{const s=simulation(t,{deadline:4000});await assert.rejects(runQwenRecovery(s.ctx),e=>e.code==='recovery_timeout'||e.code==='qwen_manual_retry_timeout');assert.ok(s.now<=4000);assert.ok(s.logs.find(l=>l.event==='adapter.retry_manual_wait').remaining_seconds<=3);});
test('DOM recovery success ends pipeline without native or manual retries',async t=>{const s=simulation(t,{completeStage:'dom_handler'});assert.equal(await runQwenRecovery(s.ctx),'complete answer');assert.equal(s.actions.includes('recoveryVisual'),false);assert.equal(s.actions.includes('recoveryWatchStart'),false);assert.ok(s.logs.some(l=>l.event==='adapter.recovery_observed'&&l.evidence==='new_generation_request'));assert.equal(s.leased,false);});
test('ongoing request is never duplicated by automatic stages',async t=>{const s=simulation(t,{pending:true,deadline:2400});await assert.rejects(runQwenRecovery(s.ctx));assert.equal(s.actions.includes('recoveryTrigger'),false);assert.equal(s.actions.includes('recoveryVisualClaim'),false);});
test('user retry starting before first recovery observation is collected without another click',async t=>{
 const s=simulation(t);s.ctx.entryCheckpoint={responseCount:1,failureCount:1};s.ctx.entrySnapshot={stopping:false,responsePending:false,recovery:{contextValid:true,currentTurnAccepted:true,currentError:true}};
 let resumed=false;
 s.ctx.network.checkpoint=()=>({responseCount:2,failureCount:1});s.ctx.network.state=()=>({observed:true,pending:0,failed:[]});
 s.ctx.observe=async()=>({stopping:false,responsePending:false,recovery:{contextValid:true,currentTurnAccepted:true,currentError:false}});
 s.ctx.resume=()=>resumed=true;s.ctx.pollComplete=()=>({done:resumed,value:'user retry answer'});
 assert.equal(await runQwenRecovery(s.ctx),'user retry answer');assert.equal(s.actions.includes('recoveryTrigger'),false);assert.equal(s.actions.includes('recoveryVisualClaim'),false);
});
test('timeout recovery can finish original still-running generation without requesting retry',async t=>{
 const s=simulation(t);s.ctx.cause.code='timeout';let polls=0,resumed=false;
 s.ctx.entrySnapshot={stopping:true,responsePending:true,recovery:{contextValid:true,currentTurnAccepted:true,currentError:false}};
 s.ctx.observe=async()=>({stopping:++polls<2,responsePending:polls<2,recovery:{contextValid:true,currentTurnAccepted:true,currentError:false}});
 s.ctx.network.state=()=>({observed:true,pending:polls<2?1:0,failed:[]});s.ctx.resume=()=>resumed=true;s.ctx.pollComplete=()=>({done:resumed&&polls>=2,value:'original answer'});
 assert.equal(await runQwenRecovery(s.ctx),'original answer');assert.equal(s.actions.includes('recoveryTrigger'),false);
});
