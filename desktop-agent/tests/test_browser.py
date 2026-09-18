import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fusion_agent.browser import BrowserTools, _OBSERVER, _READ_VALUE, _STATE
from fusion_agent.contracts import ToolError


class FakeHandle:
    def __init__(self, *, password=False, visible=True, href=None):
        self.password = password
        self.visible = visible
        self.href = href
        self.connected = True
        self.name = 'Field'
        self.text = ''
        self.disabled = False
        self.role = ''
        self.actions = []
        self.error = None
        self.value = ''
        self.ignore_value_changes = False

    def evaluate(self, script):
        if script == 'el => el.isConnected':
            return self.connected
        if script == _READ_VALUE:
            return self.value if self.connected else None
        if "getAttribute('type') === 'password'" in script:
            return self.password
        if script == "el => el.closest('a[href]')?.href || null":
            return self.href
        if not self.visible:
            return None
        return {'tag': 'input', 'type': 'password' if self.password else 'text', 'role': self.role,
                'name': self.name, 'disabled': self.disabled, 'href': self.href, '_text': self.text}

    def dispose(self):
        pass

    def _action(self, name, *args, **kwargs):
        self.actions.append((name, args, kwargs))
        if self.error:
            raise RuntimeError(self.error)

    def click(self, **kwargs):
        self._action('click', **kwargs)

    def fill(self, *args, **kwargs):
        self._action('fill', *args, **kwargs)
        if not self.ignore_value_changes:
            self.value = args[0]

    def press(self, *args, **kwargs):
        self._action('press', *args, **kwargs)

    def select_option(self, **kwargs):
        self._action('select', **kwargs)
        if not self.ignore_value_changes:
            self.value = kwargs['value']


class FakePage:
    def __init__(self, handles=None):
        self.url = 'https://example.test/path?secret=hidden#token'
        self.handles = handles if handles is not None else [FakeHandle()]
        self.state = None
        self.closed = False
        self.mouse = types.SimpleNamespace(wheel=Mock())
        self.body_text = 'Example page body'
        self.on_body_read = None
        self.http_status = 200

    def is_closed(self):
        return self.closed

    def evaluate(self, script, arg=None):
        if script == _OBSERVER:
            self.state = {'id': arg, 'revision': 0}
            return True
        if script == _STATE:
            return self.state
        raise AssertionError('Unexpected script')

    def query_selector_all(self, selector):
        return self.handles

    def locator(self, selector):
        def read(**kwargs):
            if self.on_body_read:
                self.on_body_read()
            return self.body_text
        return types.SimpleNamespace(inner_text=read)

    def title(self):
        return 'Test page'

    def goto(self, url, **kwargs):
        self.url = url
        self.state = None
        return types.SimpleNamespace(status=self.http_status) if self.http_status is not None else None

    def screenshot(self, path, **kwargs):
        Path(path).write_bytes(b'fake PNG')


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.browser = BrowserTools(Path(self.tmp.name))
        self.page = FakePage()
        self.context = types.SimpleNamespace(pages=[self.page], close=Mock())
        self.browser._context = self.context
        self.browser._page = self.page
        self.addCleanup(self.browser.close)

    def fresh(self):
        value = self.browser.snapshot({})
        return {'snapshot_id': value['snapshot_id'], 'ref': value['elements'][0]['ref']}

    def assertCode(self, code, call):
        with self.assertRaises(ToolError) as error:
            call()
        self.assertEqual(error.exception.code, code)
        return error.exception

    def test_specs_are_strict_and_capability_scoped(self):
        specs = self.browser.specs()
        self.assertEqual(len(specs), 12)
        for spec in specs:
            self.assertEqual(spec.capability, 'browser')
            self.assertFalse(spec.parameters['additionalProperties'])
        self.assertFalse(next(s for s in specs if s.name == 'browser.snapshot').mutating)
        self.assertFalse(next(s for s in specs if s.name == 'browser.verify').mutating)

    def test_snapshot_excludes_hidden_values_and_sensitive_url_parts(self):
        self.page.handles = [FakeHandle(password=True), FakeHandle(visible=False)]
        value = self.browser.snapshot({})
        self.assertEqual(value['url'], 'https://example.test/path')
        self.assertEqual(len(value['elements']), 1)
        self.assertEqual(value['elements'][0]['type'], 'password')
        self.assertNotIn('value', value['elements'][0])
        self.assertNotIn('href', value['elements'][0])
        self.assertNotIn('verification', value)

    def test_page_identity_binds_complete_url_without_disclosing_private_query(self):
        first = self.browser.snapshot({})
        checked = self.browser.verify({'url_equals': self.page.url})
        self.assertRegex(first['page_identity'], r'^[0-9a-f]{64}$')
        self.assertEqual(first['page_identity'], checked['page_identity'])
        self.assertNotIn('secret=hidden', str(first))
        self.page.url = 'https://example.test/path?secret=different#token'
        second = self.browser.snapshot({})
        self.assertEqual(first['url'], second['url'])
        self.assertNotEqual(first['page_identity'], second['page_identity'])
        self.assertNotIn('secret=different', str(second))
        opened = self.browser.open({'url': self.page.url})
        self.assertEqual(opened['page_identity'], second['page_identity'])

    def test_snapshot_truncates_text(self):
        self.page.body_text = 'x' * 19000
        value = self.browser.snapshot({})
        self.assertEqual(len(value['text']), 18000)
        self.assertTrue(value['text_truncated'])

    def test_click_once_and_snapshot_invalidated(self):
        args = self.fresh()
        result = self.browser._element_action('click', args)
        self.assertTrue(result['snapshot_required'])
        self.assertEqual(result['verification'], {'status': 'pending', 'method': 'postcondition_required', 'scope': 'browser'})
        self.assertEqual(len(self.page.handles[0].actions), 1)
        self.assertCode('stale_snapshot', lambda: self.browser._element_action('click', args))
        self.assertEqual(len(self.page.handles[0].actions), 1)

    def test_unrelated_dom_mutation_does_not_reject_unchanged_target(self):
        args = self.fresh()
        self.page.state['revision'] += 1
        self.browser._element_action('click', args)
        self.assertEqual(len(self.page.handles[0].actions), 1)

    def test_target_signature_changes_are_rejected_without_action(self):
        for field, value in [('name', 'Delete account'), ('text', 'Changed target text'),
                             ('role', 'link'), ('disabled', True), ('visible', False),
                             ('href', 'https://another.test/'), ('password', True)]:
            with self.subTest(field=field):
                handle = FakeHandle()
                self.page.handles = [handle]
                args = self.fresh()
                setattr(handle, field, value)
                self.page.state['revision'] += 1
                self.assertCode('stale_element', lambda: self.browser._element_action('click', args))
                self.assertEqual(handle.actions, [])

    def test_unrelated_mutation_during_snapshot_preserves_consistency(self):
        self.page.on_body_read = lambda: self.page.state.update(revision=1)
        args = self.fresh()
        self.browser._element_action('click', args)
        self.assertEqual(len(self.page.handles[0].actions), 1)

    def test_target_mutation_during_snapshot_requires_recapture(self):
        def mutate():
            self.page.state['revision'] += 1
            self.page.handles[0].name = 'Changed during capture'
        self.page.on_body_read = mutate
        self.assertCode('page_changed', lambda: self.browser.snapshot({}))
        self.assertIsNone(self.browser._snapshot)

    def test_same_document_navigation_rejects_old_ref(self):
        args = self.fresh()
        self.page.url = 'https://example.test/new-spa-route'
        self.assertCode('stale_snapshot', lambda: self.browser._element_action('click', args))
        self.assertEqual(self.page.handles[0].actions, [])

    def test_navigation_rejects_old_ref(self):
        args = self.fresh()
        self.page.state = None
        self.assertCode('stale_snapshot', lambda: self.browser._element_action('click', args))

    def test_disconnected_element_is_not_retried(self):
        args = self.fresh()
        self.page.handles[0].connected = False
        self.assertCode('stale_element', lambda: self.browser._element_action('click', args))
        self.assertEqual(self.page.handles[0].actions, [])

    def test_password_requires_manual_input(self):
        self.page.handles = [FakeHandle(password=True)]
        args = {**self.fresh(), 'text': 'sensitive-password'}
        error = self.assertCode('manual_input_required', lambda: self.browser._element_action('fill', args))
        self.assertNotIn('sensitive-password', str(error))
        self.assertEqual(self.page.handles[0].actions, [])

    def test_failure_does_not_echo_typed_secret_or_retry(self):
        args = {**self.fresh(), 'text': 'sensitive-value'}
        self.page.handles[0].error = 'Timeout while filling sensitive-value'
        error = self.assertCode('browser_action_failed', lambda: self.browser._element_action('fill', args))
        self.assertNotIn('sensitive-value', str(error))
        self.assertEqual(len(self.page.handles[0].actions), 1)
        self.assertIsNone(self.browser._snapshot)

    def test_fill_readback_verifies_only_actual_field_value_without_echo(self):
        result = self.browser._element_action('fill', {**self.fresh(), 'text': 'private-new-value'})
        self.assertTrue(result['ok'])
        self.assertTrue(result['value_matches'])
        self.assertEqual(result['verification'], {'status': 'verified', 'method': 'element_value', 'scope': 'browser'})
        self.assertNotIn('private-new-value', str(result))
        self.assertIsNone(self.browser._snapshot)

    def test_fill_ignored_by_page_fails_and_is_not_retried(self):
        handle = self.page.handles[0]
        handle.value = 'original-private-value'
        handle.ignore_value_changes = True
        result = self.browser._element_action('fill', {**self.fresh(), 'text': 'new-private-value'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'verification_failed')
        self.assertEqual(result['verification']['status'], 'failed')
        self.assertNotIn(handle.value, str(result))
        self.assertNotIn('new-private-value', str(result))
        self.assertEqual(len(handle.actions), 1)

    def test_select_checks_readback_instead_of_dispatch_result(self):
        for ignored in (False, True):
            with self.subTest(ignored=ignored):
                handle = FakeHandle()
                handle.ignore_value_changes = ignored
                self.page.handles = [handle]
                result = self.browser._element_action('select', {**self.fresh(), 'value': 'chosen'})
                self.assertEqual(result['ok'], not ignored)
                self.assertEqual(result['verification']['status'], 'failed' if ignored else 'verified')
                self.assertEqual(len(handle.actions), 1)

    def test_press_is_pending_despite_successful_dispatch(self):
        result = self.browser._element_action('press', {**self.fresh(), 'key': 'Enter'})
        self.assertEqual(result['verification']['status'], 'pending')
        self.assertEqual(len(self.page.handles[0].actions), 1)

    def test_open_http_error_is_failed_and_preserves_status(self):
        for status in (400, 404, 503):
            with self.subTest(status=status):
                self.page.http_status = status
                result = self.browser.open({'url': 'https://example.test/missing?secret=hidden'})
                self.assertFalse(result['ok'])
                self.assertEqual(result['http_status'], status)
                self.assertEqual(result['error']['code'], 'http_error')
                self.assertEqual(result['verification']['status'], 'failed')
                self.assertEqual(result['url'], 'https://example.test/missing')
                self.assertNotIn('secret', str(result))

    def test_open_success_or_absent_response_does_not_prove_expected_page(self):
        for status in (200, 204, None):
            with self.subTest(status=status):
                self.page.http_status = status
                result = self.browser.open({'url': 'https://example.test/'})
                self.assertEqual(result['http_status'], status)
                self.assertEqual(result['verification']['status'], 'pending')

    def test_verify_requires_actual_nonempty_expectations(self):
        for args in ({}, {'text_contains': ''}, {'text_contains': '  '}, {'text_contains': True},
                     {'success': True}, {'url_equals': ''}, {'text_contains': 'x' * 8001}):
            with self.subTest(args=args):
                self.assertCode('invalid_argument', lambda: self.browser.verify(args))

    def test_verify_reads_live_dom_and_all_supplied_assertions(self):
        self.fresh()
        self.page.body_text = 'Your update was saved.'
        result = self.browser.verify({'text_contains': 'update was saved', 'url_equals': self.page.url})
        self.assertTrue(result['ok'])
        self.assertEqual(result['checks'], {'text_contains': True, 'url_equals': True})
        self.assertEqual(result['verification'], {'status': 'verified', 'method': 'dom_assertions', 'scope': 'browser'})
        self.page.body_text = 'Update failed.'
        result = self.browser.verify({'text_contains': 'update was saved', 'url_equals': self.page.url})
        self.assertFalse(result['ok'])
        self.assertEqual(result['checks'], {'text_contains': False, 'url_equals': True})
        self.assertEqual(result['error']['code'], 'verification_failed')
        self.assertEqual(result['verification']['status'], 'failed')

    def test_verify_compares_exact_url_without_echoing_sensitive_parts(self):
        result = self.browser.verify({'url_equals': 'https://example.test/path'})
        self.assertFalse(result['ok'])
        self.assertFalse(result['checks']['url_equals'])
        result = self.browser.verify({'url_equals': self.page.url})
        self.assertTrue(result['ok'])
        self.assertEqual(result['url'], 'https://example.test/path')
        self.assertNotIn('hidden', str(result))
        self.assertNotIn('token', str(result))

    def test_verify_returns_bounded_text_but_checks_actual_read(self):
        self.page.body_text = 'x' * 19000 + ' saved confirmation'
        result = self.browser.verify({'text_contains': 'saved confirmation'})
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['text']), 18000)
        self.assertTrue(result['text_truncated'])

    def test_verify_navigation_during_read_cannot_pass(self):
        self.page.on_body_read = lambda: setattr(self.page, 'url', 'https://example.test/changed')
        result = self.browser.verify({'text_contains': 'Example'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'page_changed')
        self.assertEqual(result['verification']['status'], 'failed')

    def test_unsafe_open_urls_are_rejected(self):
        for url in ('file:///etc/passwd', 'javascript:alert(1)', 'data:text/html,hi', 'https://user:password@example.test', 'about:blank', 'https:///'):
            with self.subTest(url=url):
                self.assertCode('invalid_url', lambda: self.browser.open({'url': url}))

    def test_unsafe_link_is_not_clicked(self):
        self.page.handles[0].href = 'javascript:alert(1)'
        args = self.fresh()
        self.assertCode('invalid_url', lambda: self.browser._element_action('click', args))
        self.assertEqual(self.page.handles[0].actions, [])

    def test_scroll_invalidates_and_checks_bounds(self):
        args = {**self.fresh(), 'dy': 200}
        result = self.browser.scroll(args)
        self.assertEqual(result['verification']['status'], 'pending')
        self.page.mouse.wheel.assert_called_once_with(0, 200)
        self.assertIsNone(self.browser._snapshot)
        args = {**self.fresh(), 'dy': 99999}
        self.assertCode('invalid_argument', lambda: self.browser.scroll(args))

    def test_tab_switch_invalidates_refs(self):
        self.fresh()
        another = FakePage()
        self.context.pages.append(another)
        tabs = self.browser.tabs({})['tabs']
        self.browser.switch({'tab_id': tabs[1]['tab_id']})
        self.assertIs(self.browser._page, another)
        self.assertIsNone(self.browser._snapshot)

    def test_screenshot_is_private_local_artifact(self):
        value = self.browser.screenshot({})
        self.assertFalse(value['model_can_see_image'])
        self.assertTrue(Path(value['path']).is_file())
        self.assertEqual(Path(value['path']).stat().st_mode & 0o777, 0o600)

    def test_close_checks_tabs_are_closed(self):
        self.context.close.side_effect = lambda: setattr(self.page, 'closed', True)
        result = self.browser._close_tool({})
        self.assertTrue(result['closed'])
        self.assertEqual(result['verification']['status'], 'verified')
        self.context.close.assert_called_once()
        self.assertIsNone(self.browser._context)

    def test_close_dispatch_without_closed_tabs_fails(self):
        result = self.browser._close_tool({})
        self.assertFalse(result['ok'])
        self.assertFalse(result['closed'])
        self.assertEqual(result['verification']['status'], 'failed')
        self.context.close.assert_called_once()

    def test_close_failure_does_not_claim_closed_or_retry(self):
        self.context.close.side_effect = RuntimeError('private browser detail')
        error = self.assertCode('browser_close_failed', lambda: self.browser._close_tool({}))
        self.assertNotIn('private browser detail', str(error))
        self.context.close.assert_called_once()

    def test_optional_browser_launch_owns_profile_and_keeps_sandbox(self):
        other = BrowserTools(Path(self.tmp.name), channel='chrome')
        ctx = Mock(pages=[self.page])
        playwright = Mock()
        playwright.chromium.launch_persistent_context.return_value = ctx
        api = types.SimpleNamespace(sync_playwright=lambda: types.SimpleNamespace(start=lambda: playwright))
        with patch.dict('sys.modules', {'playwright': types.ModuleType('playwright'), 'playwright.sync_api': api}):
            other._start()
        args, kwargs = playwright.chromium.launch_persistent_context.call_args
        self.assertEqual(args, (str(Path(self.tmp.name) / 'browser-profile'),))
        self.assertTrue(kwargs['chromium_sandbox'])
        self.assertFalse(kwargs['headless'])
        self.assertNotIn('args', kwargs)
        self.assertEqual(kwargs['channel'], 'chrome')
        other.close()

    def test_browser_sandbox_disable_requires_explicit_option(self):
        other = BrowserTools(Path(self.tmp.name), sandbox=False)
        playwright = Mock()
        playwright.chromium.launch_persistent_context.return_value = Mock(pages=[self.page])
        api = types.SimpleNamespace(sync_playwright=lambda: types.SimpleNamespace(start=lambda: playwright))
        with patch.dict('sys.modules', {'playwright': types.ModuleType('playwright'), 'playwright.sync_api': api}):
            other._start()
        self.assertFalse(playwright.chromium.launch_persistent_context.call_args.kwargs['chromium_sandbox'])
        other.close()


if __name__ == '__main__':
    unittest.main()
