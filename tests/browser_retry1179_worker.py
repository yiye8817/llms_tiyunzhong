"""Real Chromium/CDP pixels+input; synthetic Qwen HTML and network notifications."""
import os
import base64
import io
import json
import sys
from PIL import Image
from playwright.sync_api import sync_playwright

HTML = r'''<style>body{font-family:Arial}textarea{width:500px;height:80px}button{height:36px;min-width:70px}.retry-disc{display:inline-flex;width:48px;height:36px;border:1px solid #666;align-items:center;justify-content:center;background:#eee}svg{width:24px;height:24px}</style>
<button id="model-selector">Qwen3.8-Max</button><main id="messages"></main><form id="composer" style="display:none"><textarea id="chat-input"></textarea><button id="send-message-button" type="button" aria-label="Send Message">Send</button></form>
<script>
const mode='MODE';window.fixtureLease=false;window.stats={sends:0,retries:0,edits:0,requests:0,blockedCommits:0,sentText:'',trusted:[]};
const input=document.querySelector('textarea');input.addEventListener('input',()=>stats.edits++);
const icon='<svg viewBox="0 0 24 24"><path d="M19 7A8 8 0 1 0 21 13M19 2v6h-6" stroke="black" stroke-width="2" fill="none"/></svg>';
function failUI(){const answer=document.querySelector('[data-role="assistant"]');answer.innerHTML='<div role="alert">Oops! There was an issue connecting to Qwen3.8-Max. 502 Bad Gateway 网络错误</div>'+(mode==='unknown'||mode==='learned'?'<div id="retry" class="retry-disc" tabindex="0">'+icon+'</div>':'<button id="retry" class="retry-disc" aria-label="Retry" type="button">'+icon+'</button>');}
async function commit(retry=false){
 if(!fixtureLease){stats.blockedCommits++;return;}
 document.documentElement.dataset.fixtureHref='https://chat.qwen.ai/c/test'+(mode==='display-query'?'?model=Qwen3.8-Max&utm_source=chat':'');
 input.value='';const rid=++stats.requests;await fixtureNetwork({phase:'start',id:rid,prompt:stats.sentText,status:retry?200:502});
 if(!retry){failUI();await fixtureNetwork({phase:'finish',id:rid});return;}
 const answer=document.querySelector('[data-role="assistant"]');answer.innerHTML='';answer.dataset.state='streaming';
 setTimeout(async()=>{delete answer.dataset.state;answer.textContent='qwen complete';await fixtureNetwork({phase:'finish',id:rid});},600);
}
document.addEventListener('click',e=>{
 const target=e.target.closest('#send-message-button,#retry');if(!target)return;
 if(target.id==='send-message-button'){
  stats.sends++;stats.sentText=input.value;
  const user=document.createElement('article');user.dataset.role='user';user.textContent=stats.sentText;
  const answer=document.createElement('article');answer.dataset.role='assistant';document.querySelector('#messages').append(user,answer);
  setTimeout(()=>commit(false),80);
 }else{
  stats.retries++;stats.trusted.push(e.isTrusted);
  if(mode==='never'||mode==='native'&&!e.isTrusted||mode==='manual'&&stats.retries<3)return;
  setTimeout(()=>commit(true),80);
 }
});
</script>'''

def emit(row):print(json.dumps(row,ensure_ascii=False),flush=True)
def main():
    with sync_playwright() as pw:
        b=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        context=b.new_context(viewport={'width':1000,'height':800});page=context.new_page();session=context.new_cdp_session(page);world=None;mode='dom'
        def event(_source,data):
            rid=f"qwen-{data['id']}";url='https://chat.qwen.ai/api/v2/chat/completions'
            if data['phase']=='start':
                emit({'event':'route','params':{'url':'https://chat.qwen.ai/c/test'+('?model=Qwen3.8-Max&utm_source=chat' if mode=='display-query' else '')}})
                emit({'event':'Network.requestWillBeSent','params':{'requestId':rid,'type':'Fetch','request':{'method':'POST','url':url,'postData':json.dumps({'messages':[{'role':'user','content':data['prompt']}]})}}})
                emit({'event':'Network.responseReceived','params':{'requestId':rid,'type':'Fetch','response':{'url':url,'status':data['status'],'mimeType':'text/event-stream' if data['status']==200 else 'application/json'}}})
            else:emit({'event':'Network.loadingFinished','params':{'requestId':rid}})
        page.expose_binding('fixtureNetwork',event)
        for line in sys.stdin:
            req=json.loads(line);ident=req['id'];method=req['method'];p=req.get('params',{})
            try:
                if os.environ.get('FUSION_TEST_VERBOSE'):print(method,file=sys.stderr,flush=True)
                if method=='start':
                    mode=p.get('mode','dom');page.set_content(HTML.replace('MODE',mode));page.evaluate("document.documentElement.dataset.fixtureHref='https://chat.qwen.ai/'")
                    result={'version':b.version}
                elif method=='close':emit({'id':ident,'result':True});break
                elif method=='lease':page.evaluate("()=>{fixtureLease=true;document.querySelector('#composer').style.display='block';}");page.bring_to_front();result=True
                elif method=='release':page.evaluate('fixtureLease=false');result=True
                elif method=='cdp':result=session.send(p['method'],p.get('params',{}))
                elif method=='stats':result=page.evaluate('stats')
                elif method=='human':
                    page.evaluate('fixtureLease=true');page.bring_to_front();page.locator('#retry').click();result=True
                elif method=='screenshot':
                    png=page.screenshot();image=Image.open(io.BytesIO(png)).convert('RGBA');result={'width':image.width,'height':image.height,'rgba':base64.b64encode(image.tobytes()).decode(),'png':base64.b64encode(png).decode()}
                elif method=='evaluate':
                    if world is None:
                        frame=session.send('Page.getFrameTree')['frameTree']['frame']['id'];world=session.send('Page.createIsolatedWorld',{'frameId':frame,'worldName':'fusion1179'})['executionContextId']
                    expression='(()=>{const location=new URL(document.documentElement.dataset.fixtureHref);return '+p['code']+';})()'
                    data=session.send('Runtime.evaluate',{'expression':expression,'contextId':world,'returnByValue':True,'awaitPromise':True,'userGesture':True})
                    if 'exceptionDetails' in data:raise RuntimeError(str(data['exceptionDetails']))
                    result=data.get('result',{}).get('value')
                else:raise ValueError(method)
                emit({'id':ident,'result':result})
            except Exception as exc:emit({'id':ident,'error':str(exc)})
        context.close();b.close()
if __name__=='__main__':main()
