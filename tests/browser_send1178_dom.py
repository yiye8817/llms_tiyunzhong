"""GLM editor discovery regression cases. Real Chromium, synthetic HTML only."""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright


def run(root, executable):
    source = (root / 'electron/provider-dom.cjs').read_text().split('module.exports')[0]
    cases = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox'])
        def case(name, html, wanted=True, generic=False, prompt="第一段\n第二段 😀"):
            page = browser.new_page(viewport={'width': 1000, 'height': 800})
            try:
                page.set_content('<style>textarea,[contenteditable=true]{width:600px;min-height:60px}button{width:100px;height:35px}</style>' + html)
                page.add_script_tag(content='(()=>{const location=new URL("https://chat.z.ai/");' + source + ';globalThis.act=pageAction;})();')
                args = {'id': name, 'provider_id': 'glm', 'selectors': {'input': ['textarea'] if generic else ['.stale-input'], 'send': ['.stale-send'], 'assistant': ['[data-role="assistant"]'], 'stop': []}, 'prompt': prompt, 'input_transport': 'cdp', 'require_interactive': True, 'deadline': int(time.time()*1000)+10000}
                cdp = page.context.new_cdp_session(page)
                cdp.send('Emulation.setFocusEmulationEnabled', {'enabled': True})
                def act(action): return page.evaluate('([a,b])=>act(a,b)', [action, args])
                report = act('prepare')
                assert bool(report.get('ready')) == wanted, report
                if wanted:
                    cdp.send('Input.insertText', {'text': args['prompt']})
                    target = act('canSubmit'); assert target['ready'], target
                    assert page.evaluate('document.querySelector("#actual").value || document.querySelector("#actual").innerText') == args['prompt']
                    claim = act('claimSubmit'); assert claim['ready'], claim
                    for typ, buttons in [('mousePressed',1), ('mouseReleased',0)]:
                        cdp.send('Input.dispatchMouseEvent', {'type':typ,'x':claim['x'],'y':claim['y'],'button':'left','buttons':buttons,'clickCount':1})
                    assert page.evaluate('window.sends') == 1
                    assert page.evaluate('[...document.querySelectorAll("textarea")].filter(x=>x.id!=="actual").every(x=>!x.value)')
                else:
                    assert page.evaluate('window.sends || 0') == 0
                    assert page.evaluate('[...document.querySelectorAll("textarea")].every(x=>!x.value)')
                cases.append({'name':name,'ok':True})
            except Exception as exc:
                cases.append({'name':name,'ok':False,'error':str(exc)})
            finally: page.close()
        # A realistic dependency trap: send only mounts after a native input.
        script = '''<script>window.sends=0;const input=document.querySelector('#actual');
input?.addEventListener('input',()=>{if(document.querySelector('#send-message-button'))return;const b=document.createElement('button');b.id='send-message-button';b.type='button';b.setAttribute('aria-label','Send Message');b.textContent='Send';b.onclick=()=>sends++;input.parentElement.append(b);});</script>'''
        for name, editor in [
            ('english_placeholder_send_appears_after_input', '<textarea id="actual" placeholder="Ask anything"></textarea>'),
            ('english_message_placeholder_before_send_exists', '<textarea id="actual" placeholder="Send a message"></textarea>'),
            ('testid_wrapper_before_send_exists', '<div data-testid="chat-input"><textarea id="actual"></textarea></div>'),
            ('testid_rich_wrapper_before_send_exists', '<div data-testid="chat-input"><div id="actual" contenteditable="true"></div></div>'),
            ('english_data_placeholder_rich_editor', '<div id="actual" contenteditable="true" data-placeholder="Ask anything"></div>'),
        ]:
            case(name, '<form>'+editor+'</form>'+script)
        case('rich_prompt_search_words_not_editor_identity', '<form><div id="actual" contenteditable="true" data-placeholder="Ask anything"></div></form>'+script, prompt='调用 web.search 搜索反馈\nDo not change this task')
        case('generic_config_excludes_sidebar_search', '<textarea placeholder="Search chats"></textarea><form><textarea id="actual" placeholder="Ask anything"></textarea></form>'+script, generic=True)
        case('generic_config_excludes_readonly_preview', '<textarea readonly></textarea><form><textarea id="actual" placeholder="Ask anything"></textarea></form>'+script, generic=True)
        case('generic_config_excludes_assistant_example', '<article data-message-role="assistant"><textarea placeholder="Ask anything"></textarea></article><form><textarea id="actual" placeholder="Ask anything"></textarea></form>'+script, generic=True)
        case('ambiguous_named_composers_not_guessed', '<textarea placeholder="Ask anything"></textarea><textarea placeholder="Ask anything"></textarea>', False)
        case('search_only_not_mistaken_for_composer', '<textarea placeholder="Search messages"></textarea>', False, generic=True)
        case('readonly_only_is_not_an_editable_composer', '<textarea placeholder="Ask anything" readonly></textarea>', False, generic=True)
        case('dialog_editor_still_excluded', '<div role="dialog"><textarea placeholder="Ask anything"></textarea></div>', False, generic=True)
        version = browser.version
        browser.close()
    return {'browser':version,'scope':'real Chromium, synthetic DOM, no provider accounts','total':len(cases),'passed':sum(c['ok'] for c in cases),'cases':cases}

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--chromium',default='/usr/bin/chromium')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=run(args.root,args.chromium)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
    raise SystemExit(report['total'] != report['passed'])
