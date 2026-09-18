'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {pageAction}=require('../electron/provider-dom.cjs');
// Deliberately small DOM boundary double: exercises the production detection
// policy without requiring npm or claiming coverage of a live site's DOM.
function inspect({message='请拖动滑块完成验证',hidden=false,inMessage=false,explicit=false,dialog=true,provider='glm',action='inspect'}={}) {
  let changed=0;
  const node={textContent:message,tagName:'DIV',
    getClientRects:()=>[{}],
    closest(selector){return (selector.includes('[hidden]')&&hidden)||(selector.includes('pre,code')&&inMessage)?{}:null;},
    matches(selector){return selector==='[role="dialog"]'?dialog:explicit&&selector.includes('[data-testid="captcha"]');},
    querySelectorAll:()=>[],getAttribute:()=>null,
    click(){changed++;}};
  const doc={querySelectorAll:selector=>selector.includes('[data-testid="verification-challenge"]')?[node]:[],
    visibilityState:'visible',hidden:false,hasFocus:()=>true,activeElement:null};
  const context={document:doc,getComputedStyle:()=>({display:'block',visibility:'visible'}),innerWidth:800,innerHeight:600,
    location:{href:'https://chat.z.ai/',origin:'https://chat.z.ai',pathname:'/',search:'',hash:''},Date,console};
  const args={provider_id:provider,selectors:{input:[],assistant:[],send:[],stop:[]},prompt:'hello',id:'test',deadline:Date.now()+10000};
  const result=vm.runInNewContext(`(${pageAction.toString()})(${JSON.stringify(action)},${JSON.stringify(args)})`,context);
  return {result,changed};
}
test('visible slider dialog pauses submission without changing DOM',()=>{
  const {result,changed}=inspect({action:'canSubmit'});
  assert.equal(result.reason,'verification_required'); assert.equal(result.verification.kind,'slider'); assert.equal(changed,0);
});
test('inspect reports verification instead of treating it as an assistant reply',()=>{
  const {result}=inspect(); assert.equal(result.verification.required,true); assert.equal(result.answers.length,0);
});
for (const options of [{hidden:true},{inMessage:true},{message:'验证成功'},{message:'Rate this response'},{provider:'qwen'}]) {
  test('no false challenge: '+JSON.stringify(options),()=>assert.equal(inspect(options).result.verification.required,false));
}
test('explicit verification widget is reported even if iframe text is unreadable',()=>{
  assert.equal(inspect({explicit:true,message:'',dialog:false}).result.verification.required,true);
});
