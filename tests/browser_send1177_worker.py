"""Isolated Chromium test worker. Routes and Network notifications are fixtures."""
import json
import sys
from playwright.sync_api import sync_playwright
HTML='''<style>textarea,[contenteditable=true]{width:650px;height:130px;overflow:auto}button{width:110px;height:36px}</style>
<button id="model-selector">MODEL</button><main id="messages"></main><form style="display:none" id="composer">EDITOR
<button id="send-message-button" aria-label="Send Message" type="button">Send</button></form>
<script>
window.fixtureLease=false;window.stats={sends:0,edits:0,requests:0,blockedCommits:0,sentText:''};
const input=document.querySelector('textarea,[contenteditable=true]');
input.addEventListener('input',()=>stats.edits++);
function value(){if('value' in input)return input.value;return [...input.querySelectorAll('p')].map(p=>p.textContent).join('\\n');}
document.querySelector('#send-message-button').addEventListener('click',()=>{
 stats.sends++;stats.sentText=value();
 const user=document.createElement('article');user.dataset.role='user';user.textContent=stats.sentText;document.querySelector('#messages').append(user);
 setTimeout(async()=>{
   if(!fixtureLease){stats.blockedCommits++;return;}
   document.documentElement.dataset.fixtureHref='ORIGINc/test';
   if('value' in input)input.value='';else input.replaceChildren();
   stats.requests++;await fixtureNetwork({phase:'start',prompt:stats.sentText});
   setTimeout(async()=>{const answer=document.createElement('article');answer.dataset.role='assistant';answer.textContent='PROVIDER complete';document.querySelector('#messages').append(answer);await fixtureNetwork({phase:'finish'});},3000);
 },400);
});
</script>'''
def emit(row):print(json.dumps(row,ensure_ascii=False),flush=True)
def main():
    with sync_playwright() as pw:
        b=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        context=b.new_context(viewport={'width':1000,'height':800});sites={}
        for line in sys.stdin:
            request=json.loads(line);ident=request['id'];method=request['method'];p=request.get('params',{});provider=p.get('provider')
            try:
                if method=='start':
                    for name,origin,editor,model in [('qwen','https://chat.qwen.ai/','<textarea id="chat-input"></textarea>','Qwen3.8-Max'),('glm','https://chat.z.ai/','<div class="tiptap" contenteditable="true"><p><br></p></div>','GLM-5.3-Flash')]:
                        page=context.new_page();sites[name]={'page':page,'origin':origin,'world':None}
                        def event(_source,data,n=name,o=origin):
                            request_id=n+'-network';url=o+'api/chat/completions'
                            if data['phase']=='start':
                                emit({'event':'route','provider':n,'params':{'url':o+'c/test'}})
                                emit({'event':'Network.requestWillBeSent','provider':n,'params':{'requestId':request_id,'type':'Fetch','request':{'method':'POST','url':url,'postData':json.dumps({'messages':[{'role':'user','content':data['prompt']}]},ensure_ascii=False)}}})
                                emit({'event':'Network.responseReceived','provider':n,'params':{'requestId':request_id,'type':'Fetch','response':{'url':url,'status':200,'mimeType':'text/event-stream'}}})
                            else:emit({'event':'Network.loadingFinished','provider':n,'params':{'requestId':request_id}})
                        page.expose_binding('fixtureNetwork',event)
                        page.set_content(HTML.replace('MODEL',model).replace('EDITOR',editor).replace('ORIGIN',origin).replace('PROVIDER',name))
                        page.evaluate('(o)=>document.documentElement.dataset.fixtureHref=o',origin)
                        sites[name]['session']=context.new_cdp_session(page)
                    result={'version':b.version}
                elif method=='close':emit({'id':ident,'result':True});break
                else:
                    site=sites[provider];page=site['page'];session=site['session']
                    if method=='lease':
                        page.evaluate("()=>{fixtureLease=true;document.querySelector('#composer').style.display='block';}");page.bring_to_front();result=True
                    elif method=='release':page.evaluate('fixtureLease=false');result=True
                    elif method=='cdp':result=session.send(p['method'],p.get('params',{}))
                    elif method=='stats':result=page.evaluate('stats')
                    elif method=='evaluate':
                        if site['world'] is None:
                            frame=session.send('Page.getFrameTree')['frameTree']['frame']['id'];site['world']=session.send('Page.createIsolatedWorld',{'frameId':frame,'worldName':'fusion1177'})['executionContextId']
                        expression='(()=>{const location=new URL(document.documentElement.dataset.fixtureHref);return '+p['code']+';})()'
                        result=session.send('Runtime.evaluate',{'expression':expression,'contextId':site['world'],'returnByValue':True,'awaitPromise':True,'userGesture':True})
                        if 'exceptionDetails' in result:raise RuntimeError(str(result['exceptionDetails']))
                        result=result.get('result',{}).get('value')
                    else:raise ValueError(method)
                emit({'id':ident,'result':result})
            except Exception as exc:emit({'id':ident,'error':str(exc)})
        context.close();b.close()
if __name__=='__main__':main()
