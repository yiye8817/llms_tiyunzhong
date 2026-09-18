"""Reuse the CDP worker with deferred Qwen retry + GLM input-before-button HTML.
All network and route events are synthetic. No logged-in account is accessed.
"""
import browser_send1177_worker as worker

worker.HTML = r'''<style>textarea,[contenteditable=true]{width:650px;min-height:80px;max-height:130px;overflow:auto}button{width:110px;height:36px}.error{padding:10px}</style>
<button id="model-selector">MODEL</button><main id="messages"></main><form style="display:none" id="composer">EDITOR</form>
<script>
const provider='PROVIDER';
window.fixtureLease=false;window.stats={sends:0,retries:0,edits:0,requests:0,blockedCommits:0,sentText:''};
const input=document.querySelector('textarea,[contenteditable=true]');
if(provider==='glm'){input.className='';input.replaceChildren();input.setAttribute('data-placeholder','Ask anything');}
function value(){if('value' in input)return input.value;return input.innerText;}
input.addEventListener('input',()=>{stats.edits++;if(!document.querySelector('#send-message-button')){
 const b=document.createElement('button');b.id='send-message-button';b.type='button';b.setAttribute('aria-label','Send Message');b.textContent='Send';document.querySelector('#composer').append(b);
}});
function delayedCommit(retry=false){setTimeout(async()=>{
 if(!fixtureLease){stats.blockedCommits++;return;}
 document.documentElement.dataset.fixtureHref='ORIGINc/test';
 if('value' in input)input.value='';else input.replaceChildren();
 stats.requests++;await fixtureNetwork({phase:'start',prompt:stats.sentText});
 const answer=document.querySelector('[data-role="assistant"]');
 if(provider==='qwen'&&!retry){
  answer.innerHTML='<div class="error" role="alert">Oops! There was an issue connecting to Qwen3.8-Max.网络错误</div><button id="retry" type="button" aria-label="Retry">Retry</button>';
  await fixtureNetwork({phase:'finish'});
 }else{
  answer.innerHTML='';answer.dataset.state='streaming';
  setTimeout(async()=>{delete answer.dataset.state;answer.textContent=provider+' complete';await fixtureNetwork({phase:'finish'});},3000);
 }
},600);}
document.addEventListener('click',event=>{
 const b=event.target.closest('button');if(!b)return;
 if(b.id==='send-message-button'){
  stats.sends++;stats.sentText=value();
  const user=document.createElement('article');user.dataset.role='user';user.textContent=stats.sentText;
  const answer=document.createElement('article');answer.dataset.role='assistant';document.querySelector('#messages').append(user,answer);
  delayedCommit();
 }else if(b.id==='retry'){stats.retries++;delayedCommit(true);}
});
</script>'''

if __name__ == '__main__':
    worker.main()
