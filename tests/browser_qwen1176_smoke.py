"""Exercise the shipped Qwen DOM recovery in Chromium without a live account.

Optional playwright + Chromium, not a runtime dependency. Location is a fixture
URL object; selection/visibility/hit testing are real browser DOM operations.
"""
import argparse
import html
import json
from pathlib import Path
import time
from playwright.sync_api import sync_playwright

NETWORK = 'Oops! There was an issue connecting to Qwen3.8-Max.网络错误'
BUSY = 'Oops! There was an issue connecting to Qwen3.8-Max. 目前服务访问量较大，请稍后再试。'
SHELL = '''<html><head><style>
body { margin: 16px; } #messages { max-width: 680px; }
article { margin: 12px 0; } .error-card { padding: 12px; width: 490px; }
.retry { width: 42px; height: 36px; margin-top: 8px; }
.retry svg { width: 18px; height: 18px; }
</style></head><body><button id="model-selector">Qwen3.8-Max</button>
<main id="messages"></main><form><textarea id="chat-input"></textarea>
<button type="button" id="send" aria-label="发送">发送</button></form></body></html>'''
ICON = '<button type="button" id="retry" class="retry"><svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 2-5"/></svg></button>'


def card(text=NETWORK, control=ICON, explicit=True):
    return '<div id="error" class="error-card"' + (' role="alert"' if explicit else '') + '>' + html.escape(text) + '</div>' + control


def run(root, executable):
    source = (root / 'electron/provider-dom.cjs').read_text().split('module.exports')[0]
    rows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox'])
        version = browser.version

        def case(name, callback, *, history='', provider='qwen'):
            page = browser.new_page(viewport={'width': 1000, 'height': 800})
            try:
                page.set_content(SHELL)
                page.evaluate('(s)=>document.querySelector("#messages").innerHTML=s', history)
                page.evaluate('''()=>{globalThis.fixtureLocation=new URL('https://chat.qwen.ai/c/test');
                    globalThis.fixtureClicks=0;document.addEventListener('click',()=>fixtureClicks++);}''')
                page.add_script_tag(content='(()=>{const location=fixtureLocation;' + source + ';globalThis.fixtureAction=pageAction;})();')
                args = {'id': 'qwen-test', 'provider_id': provider, 'prompt': '当前测试任务',
                        'selectors': {'input': ['#chat-input'], 'send': ['#send'], 'assistant': ['[data-role="assistant"]'], 'stop': []},
                        'deadline': int(time.time()*1000)+60000}

                class Site:
                    def act(self, action, **extra):
                        return page.evaluate('([a,b])=>fixtureAction(a,b)', [action, {**args, **extra}])
                    def echo(self, content=None, user=True, sibling=False):
                        content = card() if content is None else content
                        if sibling:
                            content = '<section id="turn"><article data-role="assistant">'+content+'</article></section>'
                        else:
                            content = '<article data-role="assistant">'+content+'</article>'
                        page.evaluate('''([s,user])=>{document.querySelector('textarea').value='';
                            document.querySelector('#messages').insertAdjacentHTML('beforeend',
                            (user?'<article data-role="user">当前测试任务</article>':'')+s);}''', [content, user])
                    def state(self): return self.act('recoveryInspect')
                    def js(self, script, arg=None): return page.evaluate(script, arg)
                    def expect(self, **expected):
                        result = self.state()
                        for key, value in expected.items():
                            assert result.get(key) == value, (key, value, result)
                        return result
                    def ready(self, kind='qwen_network'):
                        report = self.expect(currentTurnAccepted=True, currentError=True, contextValid=True, retryAvailable=True)
                        if kind and 'errorKind' in report: assert report['errorKind'] == kind, report
                        target = self.act('recoveryTarget')
                        assert target['ready'], target
                        assert target['target']['id'] == 'retry', target
                        return target
                site = Site()
                assert site.act('prepare')['ready']
                assert site.act('claimSubmit')['ready']
                evidence = callback(site)
                assert page.evaluate('fixtureClicks') == 0, 'DOM inspection/reservation must not click'
                assert page.locator('#model-selector').inner_text() == 'Qwen3.8-Max'
                rows.append({'name': name, 'ok': True, 'evidence': evidence})
            except Exception as exc:
                rows.append({'name': name, 'ok': False, 'error': str(exc)})
            finally:
                page.close()

        def available(s, text=NETWORK, control=ICON, explicit=True):
            s.echo(card(text, control, explicit))
            return s.ready('qwen_busy' if text == BUSY else 'qwen_network')
        case('exact_reported_network_error_with_adjacent_icon', available)
        case('existing_busy_error_compatibility', lambda s: available(s, BUSY))
        case('network_card_without_alert_role', lambda s: available(s, explicit=False))
        for label in ['重试', 'Retry', 'Try again', '重新生成回答']:
            case('named_footer_'+label, lambda s, label=label: available(s, control=f'<button type="button" id="retry" class="retry">{label}</button>'))
            case('accessible_icon_'+label, lambda s, label=label: available(s, control=ICON.replace('id="retry"', f'id="retry" aria-label="{label}"')))
        for model in ['Qwen3.8-Max', 'Qwen3.5-Plus', 'Qwen3.8-Max-Thinking']:
            case('model_label_'+model, lambda s, model=model: available(s, text=NETWORK.replace('Qwen3.8-Max', model)))
        for reason in ['网络异常', '网络连接错误', 'Network error', 'Network connection error', 'Failed to fetch']:
            case('network_reason_'+reason, lambda s, reason=reason: available(s, text=NETWORK.replace('网络错误', reason)))
        def single_claim(s):
            s.echo(); target = s.ready()
            first = s.act('recoveryClaim', nonce=target['nonce'])
            assert first['ready'], first
            assert not s.act('recoveryClaim', nonce=target['nonce'])['ready']
            assert not s.act('recoveryTarget')['ready']
            return first
        case('one_claim_only_never_dom_click', single_claim)
        def sibling(s):
            s.echo(card(control=''), sibling=True)
            s.js('(markup)=>document.querySelector("#turn").insertAdjacentHTML("beforeend",markup)', ICON)
            return s.ready()
        case('single_turn_sibling_icon_footer', sibling)
        def sibling_name(s):
            s.echo(card(control=''), sibling=True)
            s.js('(markup)=>document.querySelector("#turn").insertAdjacentHTML("beforeend",markup)', '<button id="retry" class="retry">重试</button>')
            return s.ready()
        case('single_turn_sibling_named_footer', sibling_name)
        def late_button(s):
            s.echo(card(control=''))
            s.expect(currentError=True, retryAvailable=False)
            s.js('(markup)=>document.querySelector("[data-role=assistant]").insertAdjacentHTML("beforeend",markup)', ICON)
            return s.ready()
        case('delayed_retry_button_appears', late_button)
        def unbound(s):
            s.echo(user=False)
            return s.expect(currentError=True, currentTurnAccepted=False, contextValid=False, retryAvailable=False)
        case('no_user_wrapper_never_retries', unbound)
        def unbound_plain(s):
            s.echo(card(explicit=False), user=False)
            return s.expect(currentError=True, retryAvailable=False, reason='recovery_current_error_unbound')
        case('unmarked_network_error_never_returned_as_answer', unbound_plain)
        def history(s):
            s.echo('正常回答')
            return s.expect(currentError=False, retryAvailable=False)
        case('historical_network_error_not_clicked', history,
             history='<article data-role="user">旧问题</article><article data-role="assistant">'+card()+'</article>')
        for label in ['复制', 'Like', 'Dislike', 'Send', 'Retry payment', 'New chat']:
            case('non_retry_control_'+label, lambda s, label=label: (
                s.echo(card(control=ICON.replace('id="retry"', f'id="retry" aria-label="{label}"'))),
                s.expect(currentError=True, retryAvailable=False))[1])
        def blocked(s, mutation):
            s.echo(); s.js(mutation)
            return s.expect(currentError=True, retryAvailable=False)
        for name, mutation in [
            ('disabled', 'document.querySelector("#retry").disabled=true'),
            ('hidden', 'document.querySelector("#retry").hidden=true'),
            ('obscured', '''(()=>{const r=document.querySelector('#retry').getBoundingClientRect();
               const div=document.createElement('div');div.style.cssText=`position:fixed;left:${r.left}px;top:${r.top}px;width:100px;height:80px;z-index:9999;background:white`;document.body.append(div);})()'''),
            ('ambiguous', '''document.querySelector('#retry').insertAdjacentHTML('afterend','<button class="retry"><svg><path/></svg></button>')'''),
            ('active_generation', 'document.querySelector("[data-role=assistant]").setAttribute("aria-busy","true")'),
            ('too_far', 'document.querySelector("#retry").style.marginTop="220px"'),
            ('outside_viewport', 'document.querySelector("#messages").style.marginTop="1100px"'),
        ]:
            case('blocked_'+name, lambda s, mutation=mutation: blocked(s, mutation))
        for name, mutation in [
            ('new_draft', 'document.querySelector("textarea").value="另一个问题"'),
            ('edited_user', 'document.querySelector("[data-role=user]").textContent="修改后的问题"'),
            ('different_conversation', 'fixtureLocation.href="https://chat.qwen.ai/c/other"'),
        ]:
            case('context_'+name, lambda s, mutation=mutation: (
                s.echo(), s.state(), s.js(mutation), s.expect(contextChanged=True, retryAvailable=False))[3])
        def stale(s):
            s.echo(); target=s.ready()
            s.js('const n=document.querySelector("#retry");n.replaceWith(n.cloneNode(true))')
            result=s.act('recoveryClaim', nonce=target['nonce'])
            assert not result['ready'], result
            return result
        case('changed_button_invalidates_claim', stale)
        def quoted(s):
            s.echo('<blockquote>'+html.escape(NETWORK)+'</blockquote>'+ICON)
            return s.expect(currentError=False, retryAvailable=False)
        case('quoted_error_example_not_clicked', quoted)
        def ordinary(s):
            s.echo('<p>这是正常回答。</p>'+ICON)
            return s.expect(currentError=False, retryAvailable=False)
        case('normal_answer_regenerate_icon_not_clicked', ordinary)
        def other_provider(s):
            s.echo(card())
            return s.expect(currentError=True, retryAvailable=False)
        case('qwen_icon_fallback_not_applied_to_glm', other_provider, provider='glm')
        def reveal(s):
            s.echo(); s.js('document.querySelector("#messages").style.marginTop="1100px"')
            before=s.js('scrollY')
            s.expect(currentError=True, retryAvailable=False, retryRevealable=True)
            assert s.js('scrollY')==before, 'inspection is read-only'
            report=s.act('recoveryReveal'); assert report['ready'], report
            target=s.act('recoveryTarget'); assert target['ready'], target
            assert s.act('recoveryClaim',nonce=target['nonce'])['ready']
            assert not s.act('recoveryReveal')['ready']
            return report
        case('offscreen_current_retry_revealed_then_revalidated',reveal)
        def refuse_reveal(s, mutation):
            s.echo();s.js('document.querySelector("#messages").style.marginTop="1100px"');s.js(mutation)
            before=s.js('scrollY');report=s.act('recoveryReveal')
            assert not report['ready'],report
            assert s.js('scrollY')==before
            return report
        case('offscreen_retry_different_user_not_revealed',lambda s:refuse_reveal(s,'document.querySelector("[data-role=user]").textContent="another task"'))
        case('offscreen_retry_ambiguous_not_revealed',lambda s:refuse_reveal(s,'document.querySelector("#retry").insertAdjacentHTML("afterend",\'<button aria-label="Retry">Retry</button>\')'))
        case('offscreen_retry_modal_not_revealed',lambda s:refuse_reveal(s,'document.body.insertAdjacentHTML("beforeend",\'<div role="dialog" style="position:fixed;inset:0;background:white">Modal</div>\')'))
        browser.close()
    return {'chromium_version': version, 'scope': 'Real Chromium DOM and hit tests; fixture location; no account, server or Electron',
            'total': len(rows), 'passed': sum(row['ok'] for row in rows),
            'failed': sum(not row['ok'] for row in rows), 'tests': rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = run(args.source_root, args.chromium)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output: args.output.write_text(text+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='tests'}, ensure_ascii=False))
    for row in result['tests']:
        if not row['ok']: print(row)
    return int(bool(result['failed']))


if __name__ == '__main__':
    raise SystemExit(main())
