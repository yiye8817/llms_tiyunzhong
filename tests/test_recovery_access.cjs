'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Execute the exact shipped main-process authorization and dedicated handler,
// without launching Electron or real provider pages.
const mainSource = fs.readFileSync(path.join(__dirname,'../electron/main.cjs'),'utf8');
const policySource = mainSource.slice(mainSource.indexOf('function canOpenRecoveryProvider(id)'), mainSource.indexOf('\nasync function createProvider'));
const handlerSource = mainSource.slice(mainSource.indexOf("  ipc('openRecoveryProvider',"), mainSource.indexOf("  ipc('setBounds',"));
function fixture() {
  let handler; const opened = [];
  const controller = new AbortController();
  const context = {closing:false,browserLogin:{busy:false},pendingConfigurations:0,pendingConfigRequests:0,
    providers:new Map([['qwen',{adapter:{active:true}}],['deepseek',{adapter:{active:true}}]]),
    runningJobs:new Map([['job',{provider_id:'qwen',manualRecovery:true,controller}]]),
    providerLayout:{showRecoveryProvider:id=>{opened.push(id);return {ok:true,active_provider:id};}},
    ipc:(name,callback)=>{assert.equal(name,'openRecoveryProvider');handler=callback;},
  };
  vm.runInNewContext(policySource+'\n'+handlerSource,context);
  return {context,controller,opened,open: id=>handler(id)};
}

test('dedicated recovery IPC authorizes exactly the active manual-wait provider',()=>{
  const f=fixture(); assert.equal(f.open('qwen').ok,true); assert.deepEqual(f.opened,['qwen']);
  assert.throws(()=>f.open('deepseek'),/没有等待人工重试/);
  assert.throws(()=>f.open('unknown'),/没有等待人工重试/);
  assert.throws(()=>f.open({provider_id:'qwen'}),/没有等待人工重试/);
  assert.deepEqual(f.opened,['qwen']);
});
for(const [name,mutate] of [
  ['finished job',f=>f.context.runningJobs.clear()],
  ['provider generating normally',f=>{f.context.runningJobs.get('job').manualRecovery=false;}],
  ['inactive adapter',f=>{f.context.providers.get('qwen').adapter.active=false;}],
  ['cancelled job',f=>f.controller.abort()],
  ['browser import',f=>{f.context.browserLogin.busy=true;}],
  ['queued configuration',f=>{f.context.pendingConfigurations=1;}],
  ['pending configuration request',f=>{f.context.pendingConfigRequests=1;}],
  ['application shutdown',f=>{f.context.closing=true;}],
]) test(`dedicated recovery IPC refuses ${name} without touching a view`,()=>{
  const f=fixture();mutate(f);assert.throws(()=>f.open('qwen'),/没有等待人工重试/);assert.deepEqual(f.opened,[]);
});

test('preload exposes only the dedicated provider-id recovery invocation',()=>{
  let exposed;const invokes=[];
  const requireStub=name=>{assert.equal(name,'electron');return {contextBridge:{exposeInMainWorld:(name,api)=>{assert.equal(name,'fusion');exposed=api;}},ipcRenderer:{invoke:(...args)=>{invokes.push(args);}}};};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../electron/preload.cjs'),'utf8'),{require:requireStub});
  exposed.openRecoveryProvider('qwen');assert.deepEqual(invokes,[['fusion:openRecoveryProvider','qwen']]);
});
