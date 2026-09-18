"""JSON-lines worker for the optional real-Chromium adapter smoke test.

No navigation, HTTP requests or real accounts. CDP input and DOM execution are
real; route and network events are explicit fixtures for the production adapter.
"""
import json
import sys
from playwright.sync_api import sync_playwright

HTML = '''<!doctype html><meta charset="utf-8"><style>
body{margin:24px;}#messages{width:600px;}article{margin:12px 0;}
.error{padding:12px;}#retry{width:44px;height:36px;}svg{width:18px;height:18px;}
</style><button data-testid="model-selector">Qwen3.8-Max</button>
<main id="messages"></main><form><textarea id="chat-input"></textarea>
<button type="button" id="send" aria-label="发送">发送</button></form>
<script>
window.stats={sends:0,retries:0,requests:0,manual:0};
const options=OPTIONS;
const networkText='Oops! There was an issue connecting to Qwen3.8-Max.网络错误';
function makeError(){
 const answer=document.querySelector('[data-role="assistant"]');
 answer.innerHTML='<div class="error" role="alert">'+networkText+'</div>';
 function button(){if(answer.querySelector('.error'))answer.insertAdjacentHTML('beforeend',
   '<button type="button" id="retry"><svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 2-5"/></svg></button>');}
 if(options.buttonDelay)setTimeout(button,options.buttonDelay);else button();
}
async function generate(){
 stats.requests++;
 const fail=stats.requests===1 || options.alwaysFail;
 await fixtureNetwork({attempt:stats.requests,status:fail?(options.firstStatus||200):200});
 const data={fail};
 if(data.fail){
   if(options.errorDelay)setTimeout(makeError,options.errorDelay);else makeError();
   if(options.manual && stats.requests===1)setTimeout(()=>{stats.manual++;generate();},600);
 }else document.querySelector('[data-role="assistant"]').innerHTML='<h2>Complete recovered fixture answer</h2>';
}
document.addEventListener('click',event=>{
 const button=event.target.closest('button'); if(!button)return;
 if(button.id==='send'){
  stats.sends++;
  const user=document.createElement('article');user.dataset.role='user';user.textContent=document.querySelector('textarea').value;
  document.querySelector('#messages').append(user);
  const reply=document.createElement('article');reply.dataset.role='assistant';document.querySelector('#messages').append(reply);
  document.querySelector('textarea').value='';
  document.documentElement.dataset.fixtureHref='https://chat.qwen.ai/c/original';
  fixtureRoute('https://chat.qwen.ai/c/original').then(()=>generate());
 }else if(button.id==='retry'){stats.retries++;generate();}
});
</script>'''


def emit(row):
    print(json.dumps(row, ensure_ascii=False), flush=True)


def main():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        context=None;page=None;session=None;world=None
        for line in sys.stdin:
            request=json.loads(line);identifier=request['id']
            try:
                method=request['method'];params=request.get('params',{})
                if method=='start':
                    if context:context.close()
                    context=browser.new_context(viewport={'width':1000,'height':800});world=None
                    options=params
                    page=context.new_page()
                    def fixture_network(_source, data):
                        request_id='fixture-'+str(data['attempt'])
                        url='https://chat.qwen.ai/api/v2/chat/completions'
                        emit({'event':'Network.requestWillBeSent','params':{'requestId':request_id,'type':'Fetch','request':{'method':'POST','url':url}}})
                        emit({'event':'Network.responseReceived','params':{'requestId':request_id,'type':'Fetch','response':{'url':url,'status':data['status'],'mimeType':'text/event-stream'}}})
                        emit({'event':'Network.loadingFinished','params':{'requestId':request_id}})
                    page.expose_binding('fixtureNetwork',fixture_network)
                    page.expose_binding('fixtureRoute',lambda _source,url:emit({'event':'Page.navigatedWithinDocument','params':{'url':url}}))
                    page.set_content(HTML.replace('OPTIONS',json.dumps(options)))
                    page.evaluate("document.documentElement.dataset.fixtureHref='https://chat.qwen.ai/'")
                    session=context.new_cdp_session(page)
                    session.send('Page.enable')
                    result={'url':'https://chat.qwen.ai/','browser':browser.version}
                elif method=='cdp':result=session.send(params['method'],params.get('params',{}))
                elif method=='evaluate':
                    if world is None:
                        frame=session.send('Page.getFrameTree')['frameTree']['frame']['id']
                        world=session.send('Page.createIsolatedWorld',{'frameId':frame,'worldName':'fusion-fixture-1733'})['executionContextId']
                    expression='(()=>{const location=new URL(document.documentElement.dataset.fixtureHref);return '+params['code']+';})()'
                    result=session.send('Runtime.evaluate',{'expression':expression,'contextId':world,'returnByValue':True,'awaitPromise':True,'userGesture':True})
                    if 'exceptionDetails' in result:raise RuntimeError(str(result['exceptionDetails']))
                    result=result.get('result',{}).get('value')
                elif method=='stats':result=page.evaluate('stats')
                elif method=='close':emit({'id':identifier,'result':True});break
                else:raise ValueError('Unknown command '+method)
                emit({'id':identifier,'result':result})
            except Exception as exc:emit({'id':identifier,'error':str(exc)})
        if context:context.close()
        browser.close()


if __name__=='__main__':
    main()
