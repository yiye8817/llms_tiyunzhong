import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fusion_agent.contracts import ToolError
from fusion_agent.desktop import DesktopTools
from fusion_agent.registry import ToolRegistry


class FailSafe(Exception):
    pass


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'DISPLAY': ':99', 'XDG_SESSION_TYPE': 'x11'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.desktop = DesktopTools(Path(self.tmp.name))
        self.gui = Mock()
        self.gui.FailSafeException = FailSafe
        self.gui.KEYBOARD_KEYS = ['ctrl', 'v', 'l', 'enter', 'shift']
        self.gui.size.return_value = (1200, 800)
        self.gui.position.return_value = (100, 200)
        self.gui.screenshot.return_value.save.side_effect = lambda path: Path(path).write_bytes(b'fake PNG')
        self.desktop._gui = self.gui
        self.ocr = patch('fusion_agent.desktop.shutil.which', return_value=None)
        self.ocr.start()
        self.addCleanup(self.ocr.stop)

    def fresh(self):
        return {'observation_id': self.desktop.observe({})['observation_id']}

    def assertCode(self, code, call):
        with self.assertRaises(ToolError) as error:
            call()
        self.assertEqual(error.exception.code, code)
        return error.exception

    def test_specs_are_strict_and_desktop_scoped(self):
        self.assertEqual(len(self.desktop.specs()), 8)
        for spec in self.desktop.specs():
            self.assertEqual(spec.capability, 'desktop')
            self.assertFalse(spec.parameters['additionalProperties'])
            self.assertEqual(spec.mutating, spec.name not in ('desktop.observe', 'desktop.verify'))

    def test_verify_schema_rejects_empty_or_partial_expectations(self):
        registry = ToolRegistry(self.desktop.specs(), allowed=('desktop',))
        for args in ({}, {'x': 100}, {'y': 200}, {'text_contains': 'Saved', 'x': 100},
                     {'text_contains': ''}, {'x': True, 'y': 200}, {'x': -1, 'y': 0},
                     {'observation_id': 'old', 'text_contains': 'Saved'}):
            with self.subTest(args=args):
                result = registry.invoke('desktop.verify', args)
                self.assertFalse(result['ok'])
                self.assertEqual(result['error']['code'], 'invalid_arguments')
        self.gui.screenshot.assert_not_called()
        self.gui.position.assert_not_called()

    def test_verify_direct_calls_require_concrete_expectations(self):
        for args in ({}, {'x': 100}, {'y': 200}, {'text_contains': 'Saved', 'y': 200},
                     {'text_contains': '   '}, {'text_contains': None}, {'x': 100, 'y': False},
                     {'x': 100, 'y': 200, 'success': True}, None, [], 'Saved'):
            with self.subTest(args=args):
                self.assertCode('invalid_argument', lambda: self.desktop.verify(args))
        self.gui.screenshot.assert_not_called()
        self.gui.position.assert_not_called()

    def test_verify_reads_fresh_ocr_and_normalizes_whitespace(self):
        prior = self.desktop.observe({})
        self.gui.screenshot.reset_mock()
        with patch.object(self.desktop, '_ocr', return_value={
                'available': True, 'elements': [{'text': 'Changes'}, {'text': 'saved'}]}) as ocr:
            result = self.desktop.verify({'text_contains': 'Changes\n saved'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['verification'], {'status': 'verified', 'method': 'ocr_assertions', 'scope': 'desktop'})
        self.assertTrue(result['checks']['text_contains']['matched'])
        self.assertNotEqual(result['observation']['observation_id'], prior['observation_id'])
        self.assertEqual(result['observation']['observation_id'], self.desktop._observation)
        self.gui.screenshot.assert_called_once()
        ocr.assert_called_once()

    def test_verify_text_mismatch_is_failed_even_with_screenshot(self):
        with patch.object(self.desktop, '_ocr', return_value={
                'available': True, 'elements': [{'text': 'Saving...'}]}):
            result = self.desktop.verify({'text_contains': 'Saved'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'verification_failed')
        self.assertEqual(result['verification']['status'], 'failed')
        self.assertFalse(result['checks']['text_contains']['matched'])
        self.assertTrue(Path(result['observation']['screenshot_path']).exists())

    def test_verify_ocr_unavailable_cannot_claim_screenshot_success(self):
        result = self.desktop.verify({'text_contains': 'Saved'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'ocr_unavailable')
        self.assertEqual(result['verification']['status'], 'failed')
        self.assertFalse(result['observation']['model_can_see_image'])
        self.assertTrue(Path(result['observation']['screenshot_path']).exists())

    def test_verify_pointer_reads_actual_position_without_ocr(self):
        result = self.desktop.verify({'x': 100, 'y': 200})
        self.assertTrue(result['ok'])
        self.assertEqual(result['verification'], {'status': 'verified', 'method': 'pointer_position', 'scope': 'desktop'})
        self.assertEqual(result['checks']['pointer']['actual'], {'x': 100, 'y': 200})
        self.assertIn('only the supplied assertions', result['notice'])
        self.assertIn('does not establish an application action succeeded', result['notice'])
        self.gui.position.assert_called_once()
        self.gui.moveTo.assert_not_called()
        self.gui.screenshot.assert_not_called()

    def test_verify_pointer_mismatch_reports_actual_and_fails(self):
        result = self.desktop.verify({'x': 101, 'y': 200})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'verification_failed')
        self.assertEqual(result['verification']['status'], 'failed')
        self.assertEqual(result['checks']['pointer']['actual'], {'x': 100, 'y': 200})

    def test_verify_all_supplied_expectations_must_match(self):
        for text, pointer_x, success in (('Saved', 100, True), ('Saved', 101, False), ('Saving', 100, False)):
            with self.subTest(text=text, pointer_x=pointer_x), patch.object(self.desktop, '_ocr', return_value={
                    'available': True, 'elements': [{'text': text}]}):
                result = self.desktop.verify({'text_contains': 'Saved', 'x': pointer_x, 'y': 200})
            self.assertEqual(result['ok'], success)
            self.assertEqual(result['verification']['status'], 'verified' if success else 'failed')
            self.assertEqual(set(result['checks']), {'text_contains', 'pointer'})

    def test_verify_honors_x11_and_failsafe_guards(self):
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-0'}):
            self.assertCode('wayland_unsupported', lambda: self.desktop.verify({'x': 100, 'y': 200}))
        self.gui.position.assert_not_called()
        self.fresh()
        self.gui.position.side_effect = FailSafe('sensitive backend detail')
        self.assertCode('desktop_failsafe', lambda: self.desktop.verify({'x': 100, 'y': 200}))
        self.gui.position.assert_called_once()
        self.assertIsNone(self.desktop._observation)

    def test_wayland_rejected_even_when_display_exists(self):
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-0'}):
            self.assertCode('wayland_unsupported', lambda: self.desktop.observe({}))
        self.gui.screenshot.assert_not_called()

    def test_wayland_session_type_rejected(self):
        with patch.dict(os.environ, {'XDG_SESSION_TYPE': 'wayland'}):
            self.assertCode('wayland_unsupported', lambda: self.desktop.observe({}))

    def test_missing_display_readable_error(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertCode('display_unavailable', lambda: self.desktop.observe({}))

    def test_unavailable_display_before_click_is_explicitly_not_started(self):
        registry = ToolRegistry(self.desktop.specs(), allowed=('desktop',))
        args = {**self.fresh(), 'x': 20, 'y': 20}
        with patch.dict(os.environ, {}, clear=True):
            result = registry.invoke('desktop.click', args)
        self.assertEqual(result['error']['code'], 'display_unavailable')
        self.assertEqual(result['execution'], {'status': 'not_started'})
        self.assertNotIn('outcome_unknown', result)
        self.gui.click.assert_not_called()

    def test_stale_observation_and_screen_query_errors_are_preflight(self):
        registry = ToolRegistry(self.desktop.specs(), allowed=('desktop',))
        result = registry.invoke('desktop.click', {'observation_id': 'stale', 'x': 20, 'y': 20})
        self.assertEqual(result['error']['code'], 'stale_observation')
        self.assertEqual(result['execution'], {'status': 'not_started'})
        args = {**self.fresh(), 'x': 20, 'y': 20}
        self.gui.size.side_effect = RuntimeError('display disconnected')
        result = registry.invoke('desktop.click', args)
        self.assertEqual(result['error']['code'], 'desktop_action_failed')
        self.assertEqual(result['execution'], {'status': 'not_started'})
        self.assertNotIn('outcome_unknown', result)
        self.gui.click.assert_not_called()

    def test_exception_from_actual_input_remains_uncertain(self):
        registry = ToolRegistry(self.desktop.specs(), allowed=('desktop',))
        args = {**self.fresh(), 'x': 20, 'y': 20}
        self.gui.click.side_effect = RuntimeError('input dispatch interrupted')
        result = registry.invoke('desktop.click', args)
        self.assertEqual(result['error']['code'], 'desktop_action_failed')
        self.assertEqual(result['execution'], {'status': 'unknown'})
        self.assertTrue(result['outcome_unknown'])
        self.gui.click.assert_called_once()
        self.assertIsNone(self.desktop._observation)

    def test_observe_screenshot_is_local_and_ocr_unavailable_explicit(self):
        value = self.desktop.observe({})
        self.assertEqual(value['screen']['width'], 1200)
        self.assertFalse(value['model_can_see_image'])
        self.assertFalse(value['ocr']['available'])
        path = Path(value['screenshot_path'])
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.gui.FAILSAFE)

    def test_click_once_then_requires_new_observation(self):
        args = {**self.fresh(), 'x': 100, 'y': 200}
        self.desktop.click(args)
        self.gui.click.assert_called_once_with(x=100, y=200, button='left', clicks=1, interval=0.12)
        self.assertCode('stale_observation', lambda: self.desktop.click(args))
        self.assertEqual(self.gui.click.call_count, 1)

    def test_dispatched_actions_remain_pending_until_postcondition_check(self):
        for handler, args in ((self.desktop.click, {'x': 100, 'y': 200}),
                              (self.desktop.move, {'x': 100, 'y': 200}),
                              (self.desktop.drag, {'x': 100, 'y': 200}),
                              (self.desktop.scroll, {'clicks': 2}),
                              (self.desktop.hotkey, {'keys': ['enter']}),
                              (self.desktop.type_text, {'text': 'Saved'})):
            with self.subTest(action=handler.__name__):
                result = handler({**self.fresh(), **args})
                self.assertTrue(result['performed'])
                self.assertTrue(result['observation_required'])
                self.assertEqual(result['verification'],
                                 {'status': 'pending', 'method': 'postcondition_required', 'scope': 'desktop'})
                self.assertIsNone(self.desktop._observation)
        observation = self.desktop.observe({})
        self.assertNotIn('verification', observation)
        self.gui.position.assert_not_called()

    def test_coordinates_outside_primary_screen_rejected(self):
        args = {**self.fresh(), 'x': 1200, 'y': 0}
        self.assertCode('invalid_coordinates', lambda: self.desktop.click(args))
        self.gui.click.assert_not_called()

    def test_expired_observation_rejected(self):
        args = {**self.fresh(), 'x': 20, 'y': 20}
        self.desktop._observed_at -= 181
        self.assertCode('stale_observation', lambda: self.desktop.click(args))

    def test_default_ttl_allows_slow_model_generation(self):
        args = {**self.fresh(), 'x': 20, 'y': 20}
        self.desktop._observed_at -= 90
        self.desktop.click(args)
        self.gui.click.assert_called_once()

    def test_observation_ttl_is_constructor_configuration_only(self):
        self.desktop = DesktopTools(Path(self.tmp.name), observation_ttl=60)
        self.desktop._gui = self.gui
        value = self.desktop.observe({})
        self.assertEqual(value['observation_ttl_seconds'], 60)
        args = {'observation_id': value['observation_id'], 'x': 20, 'y': 20}
        self.desktop._observed_at -= 61
        self.assertCode('stale_observation', lambda: self.desktop.click(args))
        for spec in self.desktop.specs():
            self.assertNotIn('observation_ttl', spec.parameters['properties'])

    def test_observation_ttl_rejects_unbounded_or_invalid_values(self):
        for ttl in (0, -1, float('inf'), float('nan'), True, '180'):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                DesktopTools(Path(self.tmp.name), observation_ttl=ttl)

    def test_resized_desktop_invalidates_coordinates(self):
        args = {**self.fresh(), 'x': 20, 'y': 20}
        self.gui.size.return_value = (800, 600)
        self.assertCode('screen_changed', lambda: self.desktop.click(args))
        self.assertIsNone(self.desktop._observation)

    def test_failsafe_stops_without_retry(self):
        args = {**self.fresh(), 'x': 20, 'y': 20}
        self.gui.click.side_effect = FailSafe('sensitive backend detail')
        error = self.assertCode('desktop_failsafe', lambda: self.desktop.click(args))
        self.assertNotIn('sensitive backend detail', str(error))
        self.assertEqual(self.gui.click.call_count, 1)
        self.assertIsNone(self.desktop._observation)

    def test_ascii_input_does_not_require_clipboard(self):
        args = {**self.fresh(), 'text': 'Hello'}
        self.desktop.type_text(args)
        self.gui.write.assert_called_once_with('Hello', interval=0.005)

    def test_unicode_uses_clipboard_and_restores_original(self):
        args = {**self.fresh(), 'text': '你好 Linux'}
        clipboard = types.SimpleNamespace(paste=Mock(return_value='previous clipboard'), copy=Mock())
        with patch.dict('sys.modules', {'pyperclip': clipboard}), patch('fusion_agent.desktop.time.sleep'):
            result = self.desktop.type_text(args)
        self.assertEqual(result['input_method'], 'clipboard')
        self.assertEqual(result['verification'],
                         {'status': 'pending', 'method': 'postcondition_required', 'scope': 'desktop'})
        self.gui.hotkey.assert_called_once_with('ctrl', 'v')
        self.assertEqual(clipboard.copy.call_args_list[0].args, ('你好 Linux',))
        self.assertEqual(clipboard.copy.call_args_list[1].args, ('previous clipboard',))
        self.gui.write.assert_not_called()

    def test_missing_unicode_clipboard_does_not_type_garbage(self):
        args = {**self.fresh(), 'text': '你好'}
        with patch.dict('sys.modules', {'pyperclip': None}):
            self.assertCode('clipboard_dependency_missing', lambda: self.desktop.type_text(args))
        self.gui.write.assert_not_called()
        self.gui.hotkey.assert_not_called()

    def test_clipboard_failures_do_not_echo_contents(self):
        args = {**self.fresh(), 'text': '私密文字'}
        clipboard = types.SimpleNamespace(paste=Mock(side_effect=RuntimeError('private secret')), copy=Mock())
        with patch.dict('sys.modules', {'pyperclip': clipboard}):
            error = self.assertCode('clipboard_unavailable', lambda: self.desktop.type_text(args))
        self.assertNotIn('private secret', str(error))
        self.gui.hotkey.assert_not_called()

    def test_ocr_parses_text_coordinates_and_skips_low_confidence(self):
        tsv = 'level\tleft\ttop\twidth\theight\tconf\ttext\n5\t100\t50\t80\t20\t94.2\t浏览器\n5\t0\t0\t1\t1\t10\tnoise\n'
        with patch('fusion_agent.desktop.shutil.which', return_value='/usr/bin/tesseract'), patch('fusion_agent.desktop.subprocess.run', side_effect=[types.SimpleNamespace(stdout='List of available languages (2):\neng\nchi_sim\n'), types.SimpleNamespace(stdout=tsv)]) as run:
            value = self.desktop.observe({})
        self.assertTrue(value['ocr']['available'])
        self.assertEqual(value['ocr']['languages'], ['eng', 'chi_sim'])
        self.assertEqual(len(value['ocr']['elements']), 1)
        self.assertEqual(value['ocr']['elements'][0]['x'], 100)
        self.assertEqual(value['ocr']['elements'][0]['text'], '浏览器')
        for call in run.call_args_list:
            self.assertNotIn('shell', call.kwargs)
            self.assertLessEqual(call.kwargs['timeout'], 15)

    def test_ocr_timeout_retains_screenshot_without_visual_claim(self):
        import subprocess
        with patch('fusion_agent.desktop.shutil.which', return_value='/usr/bin/tesseract'), patch('fusion_agent.desktop.subprocess.run', side_effect=subprocess.TimeoutExpired('tesseract', 5)):
            value = self.desktop.observe({})
        self.assertFalse(value['ocr']['available'])
        self.assertTrue(Path(value['screenshot_path']).exists())
        self.assertFalse(value['model_can_see_image'])

    def test_invalid_hotkey_is_not_dispatched(self):
        args = {**self.fresh(), 'keys': ['made-up-key']}
        self.assertCode('invalid_argument', lambda: self.desktop.hotkey(args))
        self.gui.hotkey.assert_not_called()


if __name__ == '__main__':
    unittest.main()
