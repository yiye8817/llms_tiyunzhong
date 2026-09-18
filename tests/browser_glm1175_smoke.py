"""Local Chromium DOM fixtures for the serialized production pageAction.

No real site/account/request. Location is an injected URL object so this also
runs where managed Chromium forbids navigations. Does not test Electron itself.
Requires optional playwright; not part of unittest discovery.
"""
import argparse
import html
import json
from pathlib import Path
import time
from playwright.sync_api import sync_playwright

HTML = '''<html><body><button id="model-selector">GLM-5.3-Flash</button>
<main id="messages"></main><form><textarea id="chat-input"></textarea>
<button type="button" id="send" aria-label="Send Message">Send</button></form></body></html>'''


def run(root, executable):
    source = (root / 'electron/provider-dom.cjs').read_text().split('module.exports')[0]
    rows = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox'])
        version = browser.version
        def case(name, callback, *, url='https://chat.z.ai/', prompt='当前测试任务', fresh=False):
            page = browser.new_page()
            try:
                page.set_content(HTML)
                page.evaluate('(url)=>{globalThis.fixtureLocation=new URL(url);globalThis.fixtureClicks=0;document.addEventListener("click",()=>fixtureClicks++);}',url)
                page.add_script_tag(content='(() => { const location=globalThis.fixtureLocation;'+source+';globalThis.fixtureAction=pageAction;})();')
                args={'id':'fixture','provider_id':'glm','prompt':prompt,'fresh_session_confirmed':fresh,
                    'selectors':{'input':['#chat-input'],'send':['#send'],'assistant':['[data-role="assistant"]'],'stop':[]},
                    'deadline':int(time.time()*1000)+60000}
                class Site:
                    def act(self,action):return page.evaluate('([a,b])=>fixtureAction(a,b)',[action,args])
                    def route(self,path):page.evaluate('(p)=>{fixtureLocation.href=new URL(p,fixtureLocation.href).href;}',path)
                    def echo(self,user=None,answer='正常服务器回复',markup=None):
                        content=markup if markup is not None else html.escape(prompt if user is None else user)
                        page.evaluate('(s)=>{document.querySelector("textarea").value="";document.querySelector("#messages").innerHTML=s;}',
                            '<article data-role="user">'+content+'</article><article data-role="assistant">'+answer+'</article>')
                    def state(self):return self.act('recoveryInspect')
                    def js(self,script):return page.evaluate(script)
                site=Site()
                assert site.act('prepare')['ready'];assert site.act('claimSubmit')['ready']
                evidence=callback(site)
                assert page.locator('#model-selector').inner_text()=='GLM-5.3-Flash'
                assert page.evaluate('fixtureClicks')==0,'inspection must never click/resubmit'
                rows.append({'name':name,'ok':True,'evidence':evidence})
            except Exception as exc:
                rows.append({'name':name,'ok':False,'error':str(exc)})
            finally:page.close()
        def require(state,**expected):
            for k,v in expected.items():assert state.get(k)==v,(k,state)
            return state
        def echo_before_route(s):
            s.echo();before=require(s.state(),contextValid=True,contextChanged=False)
            s.route('/c/new-thread');after=require(s.state(),contextValid=True,contextChanged=False,currentTurnAccepted=True)
            assert s.act('inspect')['answers'][-1]['rawText']=='正常服务器回复'
            return {'before':before,'after':after}
        case('optimistic_echo_before_first_conversation_url',echo_before_route)
        def route_before_echo(s):
            s.route('/c/new-thread');require(s.state(),contextChanged=False,currentTurnAccepted=False)
            s.echo();return require(s.state(),contextValid=True,currentTurnAccepted=True)
        case('route_before_user_bubble',route_before_echo)
        def second_route(s):
            s.echo();s.route('/c/first');require(s.state(),contextValid=True)
            s.route('/c/other');return require(s.state(),contextChanged=True,reason='recovery_conversation_changed')
        case('second_conversation_is_rejected',second_route)
        def multiple_pending(s):
            s.route('/c/one');require(s.state(),contextChanged=False)
            s.route('/c/two');return require(s.state(),contextChanged=True)
        case('second_route_before_echo_is_rejected',multiple_pending)
        def back_to_launch(s):
            s.route('/c/one');s.state();s.route('/');return require(s.state(),contextChanged=True)
        case('pending_route_back_to_home_is_rejected',back_to_launch)
        def wrong_user(s):
            s.echo(user='不同的输入');return require(s.state(),contextChanged=True,reason='recovery_user_changed')
        case('different_user_input_is_rejected',wrong_user)
        def second_user(s):
            s.echo();s.state();s.js('document.querySelector("#messages").insertAdjacentHTML("beforeend",\'<article data-role="user">another</article>\')')
            return require(s.state(),contextChanged=True,reason='recovery_user_history_changed')
        case('second_user_bubble_is_rejected',second_user)
        def draft(s):
            s.echo();s.js('document.querySelector("textarea").value="changed draft"')
            return require(s.state(),contextChanged=True,reason='recovery_input_changed')
        case('different_draft_is_rejected',draft)
        def same_existing(s):
            s.echo();return require(s.state(),contextValid=True,currentTurnAccepted=True)
        case('existing_conversation_remains_valid',same_existing,url='https://chat.z.ai/c/existing')
        def changed_existing(s):
            s.echo();s.state();s.route('/c/different');return require(s.state(),contextChanged=True)
        case('existing_conversation_change_is_rejected',changed_existing,url='https://chat.z.ai/c/existing')
        case('confirmed_new_chat_with_late_route',echo_before_route,url='https://chat.z.ai/c/previous',fresh=True)
        def origin(s):
            s.echo();s.route('https://other.invalid/c/new');return require(s.state(),contextChanged=True,reason='recovery_origin_changed')
        case('other_origin_is_rejected',origin)
        def multiline(s):
            s.echo(markup='<p>第一行</p><p>第二行<br>第三行</p><button>Copy</button>')
            state=require(s.state(),contextValid=True,lastUserMatchesPrompt=True)
            assert s.act('inspect')['lastUserMatchesPrompt']
            s.route('/c/multi');return require(s.state(),contextValid=True)
        case('paragraph_br_and_copy_button_preserve_prompt',multiline,prompt='第一行\n第二行\n第三行')
        def remount(s):
            s.echo();s.state();s.echo();return require(s.state(),contextValid=True)
        case('identical_user_dom_remount_is_not_new_input',remount)
        def no_error(s):
            s.echo(answer='A normal answer, not an error');return require(s.state(),contextValid=True,currentError=False)
        case('normal_response_is_collectable',no_error)
        def real_error(s):
            s.echo(answer='<div role="alert">Server error</div>');return require(s.state(),currentError=True,currentTurnAccepted=True)
        case('real_server_error_is_not_answer',real_error)
        def no_evidence(s):
            s.js('document.querySelector("textarea").value=""');return require(s.state(),contextChanged=False,currentTurnAccepted=False)
        case('missing_user_wrapper_stays_unknown_not_success',no_evidence)
        def unbound_home(s):
            s.echo()
            for _ in range(3):state=require(s.state(),contextValid=True)
            s.route('/c/assigned');return require(s.state(),contextValid=True)
        case('homepage_polls_do_not_pin_launch_url',unbound_home)
        browser.close()
    return {'chromium_version':version,'scope':'Real Chromium DOM; mocked location; no live account or network; no Electron',
        'total':len(rows),'passed':sum(r['ok'] for r in rows),'failed':sum(not r['ok'] for r in rows),'tests':rows}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--chromium',default='/usr/bin/chromium')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=run(args.source_root,args.chromium)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:args.output.write_text(text+'\n')
    print(text)
    return int(bool(result['failed']))
if __name__=='__main__':raise SystemExit(main())
