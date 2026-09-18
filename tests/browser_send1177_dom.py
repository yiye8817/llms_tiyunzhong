"""Real Chromium DOM + native Input.insertText fixtures, no account or site traffic."""
import argparse
import json
from pathlib import Path
import time
from playwright.sync_api import sync_playwright

def run(root, executable):
    source=(root/'electron/provider-dom.cjs').read_text().split('module.exports')[0]
    rows=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
        def case(name,body,callback,prompt='第一行\n第二行 "quoted"\n😀',provider='glm'):
            page=browser.new_page(viewport={'width':1000,'height':800})
            try:
                page.set_content('<style>textarea,[contenteditable=true]{width:650px;min-height:70px}button{width:80px;height:36px}</style>'+body)
                page.evaluate("document.documentElement.dataset.fixtureHref='https://chat.z.ai/'")
                page.add_script_tag(content='(()=>{const location=new URL(document.documentElement.dataset.fixtureHref);'+source+';globalThis.act1177=pageAction;})();')
                page.evaluate("()=>{globalThis.fixtureClicks=0;document.addEventListener('click',()=>fixtureClicks++);}")
                args={'id':name,'provider_id':provider,'selectors':{'input':['.stale-input'],'send':['.stale-send'],'assistant':['[data-role="assistant"]'],'stop':[]},'prompt':prompt,'input_transport':'cdp','require_interactive':True,'deadline':int(time.time()*1000)+60000}
                session=page.context.new_cdp_session(page)
                session.send('Emulation.setFocusEmulationEnabled',{'enabled':True})
                def act(action,**extra):return page.evaluate('([a,b])=>act1177(a,b)',[action,{**args,**extra}])
                callback(page,session,act,prompt)
                rows.append({'name':name,'ok':True})
            except Exception as exc: rows.append({'name':name,'ok':False,'error':str(exc)})
            finally:page.close()
        def send(page,cdp,act,prompt):
            prepared=act('prepare');assert prepared['ready'],prepared
            cdp.send('Input.insertText',{'text':prompt})
            result=act('canSubmit');assert result['ready'],result
            claimed=act('claimSubmit');assert claimed['ready'],claimed
            for typ,buttons in [('mousePressed',1),('mouseReleased',0)]:
                cdp.send('Input.dispatchMouseEvent',{'type':typ,'x':claimed['x'],'y':claimed['y'],'button':'left','buttons':buttons,'clickCount':1})
            assert page.evaluate('fixtureClicks')==1
        send_button='<button id="send-button" type="button" aria-label="Send">↑</button>'
        case('glm_plain_contenteditable_id', '<div id="chat-input" contenteditable="true"></div>'+send_button, send)
        case('glm_ProseMirror_no_role_multiline', '<div class="ProseMirror" contenteditable="true"><p><br></p></div>'+send_button, send)
        case('glm_tiptap_no_role_multiline', '<div class="tiptap" contenteditable="true"><p><br></p></div>'+send_button, send)
        case('glm_wrapper_id_contains_editor', '<div id="chat-input"><div contenteditable="true"></div></div>'+send_button, send)
        case('glm_english_unique_textarea', '<textarea placeholder="Ask anything"></textarea>'+send_button, send)
        case('glm_ignores_reply_fake_editor', '<article data-role="assistant"><textarea id="chat-input"></textarea><button id="send-message-button">Send</button></article><div class="ProseMirror" contenteditable="true"><p><br></p></div>'+send_button, send)
        def no_editor(page,cdp,act,prompt):
            result=act('prepare');assert not result['ready'],result;assert page.evaluate('fixtureClicks')==0
        case('ambiguous_plain_editors_not_guessed', '<textarea placeholder="one"></textarea><textarea placeholder="two"></textarea>'+send_button,no_editor)
        case('search_dialog_not_used_as_composer', '<div role="dialog"><textarea placeholder="Search"></textarea>'+send_button+'</div>',no_editor)
        def limit(page,cdp,act,prompt):
            result=act('prepare');assert result['reason']=='input_too_long',result
            assert page.locator('textarea').input_value()==''
        case('maxlength_rejected_without_truncation', '<textarea id="chat-input" maxlength="5"></textarea>'+send_button,limit)
        def chunks(page,cdp,act,prompt):
            assert act('prepare')['ready']
            # Native Chromium emits an input event for every segment, but no send.
            offset=0
            for part in [prompt[i:i+1800] for i in range(0,len(prompt),1800)]:
                report=act('inputProgress',input_offset=offset);assert report['ready'],report
                cdp.send('Input.insertText',{'text':part})
                offset+=len(part.encode('utf-16-le'))//2
                report=act('inputProgress',input_offset=offset);assert report['ready'],report
            report=act('canSubmit');assert report['ready'],report;assert page.evaluate('fixtureClicks')==0
        long=('Agent schema: {"tool":"python.run","code":"print(42)"}\n中文😀\n'*1000)
        case('qwen_66000bytes_native_chunks_exact', '<textarea id="chat-input"></textarea>'+send_button,chunks,prompt=long,provider='qwen')
        case('glm_rich_editor_long_single_native_edit_exact', '<div class="tiptap" contenteditable="true"><p><br></p></div>'+send_button,send,prompt=long)
        def caret(page,cdp,act,prompt):
            assert act('prepare')['ready'];cdp.send('Input.insertText',{'text':prompt})
            page.evaluate('document.querySelector("textarea").setSelectionRange(0,0)')
            result=act('inputProgress',input_offset=len(prompt.encode('utf-16-le'))//2)
            assert result['reason']=='input_caret_changed',result
        case('user_caret_change_stops_append', '<textarea id="chat-input"></textarea>'+send_button,caret)
        def prefix(page,cdp,act,prompt):
            assert act('prepare')['ready'];cdp.send('Input.insertText',{'text':'different'})
            assert act('inputProgress',input_offset=3)['reason']=='prefix_changed'
        case('unexpected_draft_change_stops_append', '<textarea id="chat-input"></textarea>'+send_button,prefix)
        def disabled(page,cdp,act,prompt):
            assert act('prepare')['ready'];cdp.send('Input.insertText',{'text':prompt})
            result=act('canSubmit');assert result['reason']=='send_disabled',result
        case('disabled_GLM_send_never_enter_fallback','<textarea id="chat-input"></textarea><button id="send-button" disabled>↑</button>',disabled)
        def hidden(page,cdp,act,prompt):
            assert not act('inspect')['inputReady'];page.evaluate("document.querySelector('form').style.display='block'")
            send(page,cdp,act,prompt)
        case('GLM_editor_mounted_when_revealed','<form style="display:none"><div class="tiptap" contenteditable="true"><p><br></p></div>'+send_button+'</form>',hidden)
        version=browser.version;browser.close()
    return {'browser':version,'source_root':str(root),'passed':sum(x['ok'] for x in rows),'total':len(rows),'cases':rows}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--chromium',default='/usr/bin/chromium');parser.add_argument('--output',type=Path,required=True);a=parser.parse_args()
    result=run(a.root,a.chromium);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['passed']==result['total'] else 1)
