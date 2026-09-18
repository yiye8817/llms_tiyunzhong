'use strict';
// Dependency-free execution of the production progress reducer/render functions.
// Full renderer and real CSS selector tests remain in test_ui/test_glm_session.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../ui/app.js'), 'utf8');
class Element {
  constructor(content = '') {this.textContent=content; this.children=[]; this.dataset={}; this.hidden=false;}
  append(...children) {this.children.push(...children);}
  get lastElementChild() {return this.children.at(-1);}
}
function harness() {
  const nodes=Object.fromEntries(['model-progress','model-progress-summary','model-progress-rows'].map(id=>[id,new Element()]));
  const run={progressId:'test',sequence:0,phase:'queued',timeout:60,candidateStarted:0,providers:{chatgpt:'queued',glm:'queued'}};
  const state={run,busy:true};
  const slice=source.slice(source.indexOf('  const progressNames ='),source.indexOf('  function updateControls()'));
  const context={state,Date,Object,Number,Array,Math,api:{request:async()=>({events:[]})},
    $:id=>nodes[id], text:(_tag,content)=>new Element(content),providerName:id=>id};
  vm.runInNewContext(slice+'\nglobalThis.apply=applyRunProgress; globalThis.render=renderRunProgress; globalThis.poll=pollRunProgress;',context);
  return {context,state,run,nodes};
}
test('first candidate saved shows 1/2 and waits, not fusion; new job stages cannot overwrite saved candidates',()=>{
  const h=harness();
  h.context.apply(h.run,{events:[{sequence:1,stage:'candidates_started'},{sequence:2,stage:'candidate_saved',purpose:'candidate',provider:'chatgpt'},
    {sequence:3,stage:'generating',purpose:'candidate',provider:'glm'}, {sequence:4,stage:'collecting',purpose:'candidate',provider:'chatgpt'}]});
  assert.match(h.nodes['model-progress-summary'].textContent,/1\/2 已完成.*等待 glm/);
  assert.equal(h.run.phase,'candidates'); assert.equal(h.run.providers.chatgpt,'candidate_saved');
  assert.equal(h.nodes['model-progress-rows'].children.length,2);
});
test('fusion phase is explicit and does not reset candidate state when fusion reuses GLM',()=>{
  const h=harness();
  h.context.apply(h.run,{events:[{sequence:1,stage:'candidate_saved',provider:'chatgpt',purpose:'candidate'},
    {sequence:2,stage:'candidate_saved',provider:'glm',purpose:'candidate'}, {sequence:3,stage:'fusion_started'},
    {sequence:4,stage:'generating',purpose:'fusion',provider:'glm'}]});
  assert.match(h.nodes['model-progress-summary'].textContent,/2\/2.*正在整合.*glm/);
  assert.equal(h.run.providers.glm,'candidate_saved');
});
test('late or wrong-request progress cannot alter active run',()=>{
  const h=harness(); h.context.apply({...h.run},{events:[{sequence:1,stage:'fusion_started'}]});
  assert.equal(h.run.phase,'queued');
  h.context.apply(h.run,{events:[{sequence:4,stage:'candidate_timed_out',purpose:'candidate',provider:'glm'}]});
  h.context.apply(h.run,{events:[{sequence:3,stage:'candidate_saved',purpose:'candidate',provider:'glm'},
    {sequence:5,stage:'generating',purpose:'candidate',provider:'glm'}]});
  assert.equal(h.run.providers.glm,'candidate_timed_out');
});
test('verification and status-sync errors are visible without retrying chat',async()=>{
  const h=harness(); let calls=0;
  h.context.apply(h.run,{events:[{sequence:1,stage:'verification_required',purpose:'candidate',provider:'glm'}]});
  assert.equal(h.nodes['model-progress-rows'].children[1].lastElementChild.textContent,'等待人工验证');
  h.context.api.request=async(method)=>{calls++; assert.equal(method,'GET'); throw Error('poll failed');};
  await h.context.poll(); assert.equal(calls,1); assert.equal(h.run.pollPending,false);
  assert.match(h.nodes['model-progress-summary'].textContent,/状态同步暂不可用/);
});

test('Qwen 3-stage details and countdown render in the chat panel, not just provider tooltip',()=>{
  const h=harness();h.run.providers={qwen:'queued',glm:'queued'};
  for(const [i,stage,label] of [[1,'dom_handler','第 1/3 级'],[2,'screenshot_click','第 2/3 级'],[3,'manual','第 3/3 级']]) {
    h.context.apply(h.run,{events:[{sequence:i,stage:stage==='manual'?'manual_retry_required':'retrying',purpose:'candidate',provider:'qwen',retry_stage:stage,remaining_seconds:20}]});
    assert.ok(h.nodes['model-progress-rows'].children[0].lastElementChild.textContent.includes(label));
  }
  assert.match(h.nodes['model-progress-rows'].children[0].lastElementChild.textContent,/剩余 20 秒/);
  h.context.apply(h.run,{events:[{sequence:4,stage:'manual_retry_required',purpose:'candidate',provider:'qwen',retry_stage:'manual',remaining_seconds:19}]});
  assert.match(h.nodes['model-progress-rows'].children[0].lastElementChild.textContent,/剩余 19 秒/);
});
test('generic retry update does not erase the current named stage',()=>{
  const h=harness();h.run.providers={qwen:'queued'};
  h.context.apply(h.run,{events:[{sequence:1,stage:'retrying',purpose:'candidate',provider:'qwen',retry_stage:'screenshot_click'},{sequence:2,stage:'retrying',purpose:'candidate',provider:'qwen'}]});
  assert.match(h.nodes['model-progress-rows'].children[0].lastElementChild.textContent,/第 2\/3 级/);
});
test('deadline skips are visible and cannot overwrite a completed original',()=>{
  const h=harness();h.run.providers={qwen:'queued'};
  h.context.apply(h.run,{events:[{sequence:1,stage:'retry_unavailable',purpose:'candidate',provider:'qwen',retry_reason:'total_deadline_expired'}]});
  assert.match(h.nodes['model-progress-rows'].children[0].lastElementChild.textContent,/总时限已耗尽/);
  h.context.apply(h.run,{events:[{sequence:2,stage:'candidate_saved',purpose:'candidate',provider:'qwen'},{sequence:3,stage:'retrying',purpose:'candidate',provider:'qwen',retry_stage:'dom_handler'}]});
  assert.equal(h.run.providers.qwen,'candidate_saved');
});
