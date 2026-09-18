"""Regressions for the attached run's false pending-browser deadlock.

All browser/network/install outcomes here are local fixtures. No recorded user
commands are replayed and no real video is downloaded.
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fusion_agent.browser import BrowserTools
from fusion_agent.contracts import ToolError
from fusion_agent.environment import EnvironmentTools
from fusion_agent.local_tools import LocalTools
from fusion_agent.registry import ToolRegistry
from fusion_agent.runtime import Runtime
from test_browser import FakePage
from test_runtime import Registry, ScriptedClient, action, final


class DependencyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_dir = self.root / 'run'

    def state(self):
        return json.loads((self.run_dir / 'state.json').read_text())

    def test_missing_import_is_not_a_navigation_with_unknown_effects(self):
        browser = BrowserTools(self.root)
        self.addCleanup(browser.close)
        registry = ToolRegistry(browser.specs(), allowed=['browser'])
        with patch.dict('sys.modules', {'playwright': None, 'playwright.sync_api': None}):
            result = registry.invoke('browser.open', {'url': 'https://example.test/'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'dependency_missing')
        self.assertEqual(result['execution']['status'], 'not_started')
        self.assertNotIn('outcome_unknown', result)
        self.assertEqual(result['error']['details']['repair_tool'], 'environment.browser_setup')
        self.assertIsNone(browser._page)

    def test_actual_navigation_timeout_still_has_unknown_effects(self):
        browser = BrowserTools(self.root)
        self.addCleanup(browser.close)
        browser._context = type('Context', (), {'close': lambda *_: None})()
        browser._page = FakePage()
        registry = ToolRegistry(browser.specs(), allowed=['browser'])
        with patch.object(browser._page, 'goto', side_effect=TimeoutError):
            result = registry.invoke('browser.open', {'url': 'https://example.test/'})
        self.assertTrue(result['outcome_unknown'])
        self.assertEqual(result['error']['code'], 'navigation_failed')

    def test_dependency_repair_retries_navigation_then_verifies_and_continues_task(self):
        browser = BrowserTools(self.root)
        local = LocalTools(self.root / 'workspace', self.root)
        self.addCleanup(browser.close)
        self.addCleanup(local.close)
        page = FakePage()
        ready = False
        navigations = []
        original_goto = page.goto

        def goto(url, **kwargs):
            navigations.append(url)
            return original_goto(url, **kwargs)

        page.goto = goto

        def start():
            if not ready:
                raise ToolError('dependency_missing', 'Current Agent interpreter lacks Playwright.', not_executed=True)
            browser._context = type('Context', (), {'pages': [page], 'close': lambda *_: None})()
            browser._page = page

        installed_commands = []
        def install(arguments):
            nonlocal ready
            installed_commands.append(arguments['argv'])
            ready = True
            return {'ok': True, 'returncode': 0, 'stdout': 'fixture dependency installed', 'stderr': ''}

        def inspect():
            return {'dependency_ready': ready, 'package_ready': ready, 'browser_binary_ready': ready,
                    'python_executable': '/fixture-venv/bin/python',
                    **({} if ready else {'check_error': {'code': 'dependency_missing', 'message': 'Fixture package missing'}})}

        events = []
        environment = EnvironmentTools(self.root, install, event=lambda name, fields: events.append((name, fields)))
        registry = ToolRegistry([*browser.specs(), *local.specs(), *environment.specs()], allowed=['browser', 'shell', 'files'])
        replies = [action('browser.open', {'url': 'https://example.test/'}),
                   action('environment.browser_check'),
                   action('environment.browser_setup'),
                   action('shell.run', {'argv': ['/bin/true']}),
                   action('browser.open', {'url': 'https://example.test/'}),
                   action('browser.snapshot'),
                   action('browser.verify', {'url_equals': 'https://example.test/', 'text_contains': 'Example page body'}),
                   action('files.write', {'path': 'checked.txt', 'content': 'fixture page verified'}),
                   action('files.read', {'path': 'checked.txt'}), final('网页和本地结果均已核验')]
        with (patch.object(browser, '_start', side_effect=start),
              patch.object(environment, '_inspect', side_effect=inspect),
              patch('fusion_agent.environment.sys.prefix', '/fixture-venv'),
              patch('fusion_agent.environment.sys.base_prefix', '/fixture-system'),
              patch('fusion_agent.environment.sys.executable', '/fixture-venv/bin/python'),
              patch('fusion_agent.environment.importlib.util.find_spec', return_value=object())):
            result = Runtime(ScriptedClient(replies), registry, self.run_dir,
                             event=lambda name, fields: events.append((name, fields))).run(
                                 '打开网页 https://example.test/ \n'
                                 '如缺依赖，在命令行安装浏览器依赖并运行命令检查环境；'
                                 '核验页面后把结果保存到文件 checked.txt 并回读')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(navigations, ['https://example.test/'])
        self.assertEqual(installed_commands, [['/fixture-venv/bin/python', '-m', 'pip', 'install', 'playwright>=1.50,<2']])
        self.assertEqual(self.state()['pending_verifications'], {})
        self.assertEqual(self.state()['unresolved_failures'], [])
        self.assertEqual(self.state()['uncertain_actions'], [])
        self.assertEqual((self.root / 'workspace/checked.txt').read_text(), 'fixture page verified')
        self.assertFalse(any(name == 'verification.required' for name, _ in events))
        self.assertTrue(any(name == 'environment.command.completed' for name, _ in events))
        failure = self.state()['failed_actions'][0]
        self.assertEqual(failure['execution_status'], 'not_started')
        self.assertEqual(failure['resolution_method'], 'dom_assertions')

    def test_dependency_installation_alone_cannot_complete_unopened_page_task(self):
        class MissingRegistry(Registry):
            def catalog(self):
                return [{'name': 'browser.open', 'capability': 'browser', 'mutating': True}]

            def invoke(self, *_):
                return {'ok': False, 'execution': {'status': 'not_started'},
                        'error': {'code': 'dependency_missing', 'message': 'Playwright missing in current interpreter'}}

        result = Runtime(ScriptedClient([action('browser.open', {'url': 'https://example.test/'}), final('已完成')]),
                         MissingRegistry(), self.run_dir).run('打开网页 https://example.test/')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'tool_failed')
        self.assertEqual(self.state()['pending_verifications'], {})
        self.assertEqual(self.state()['uncertain_actions'], [])
        self.assertIn('Playwright missing in current interpreter', result['answer'])
        self.assertIn('请求尚未执行', (self.run_dir / 'result.md').read_text())

    def test_separate_pending_actions_have_separate_verification_repair_budgets(self):
        class ClickRegistry(Registry):
            def catalog(self):
                return [{'name': name, 'capability': 'browser', 'mutating': name == 'browser.click'}
                        for name in ('browser.click', 'browser.verify')]

            def invoke(self, name, args):
                self.calls.append((name, args))
                return {'ok': True, 'verification': {'status': 'pending' if name == 'browser.click' else 'verified',
                                                       'scope': 'browser', 'method': 'dom_assertions'}}

        registry = ClickRegistry()
        replies = [action('browser.click'), final(), final(), action('browser.verify'),
                   action('browser.click'), final(), final(), action('browser.verify'), final()]
        result = Runtime(ScriptedClient(replies), registry, self.run_dir).run(
            '请点击网页按钮两次并分别核验')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(registry.calls), 4)

    def test_invalid_json_stops_before_later_server_actions(self):
        registry = Registry()
        read = action(arguments={'path': 'target.txt'})
        replies = ['invalid', read, 'invalid', read, 'invalid', read, final()]
        result = Runtime(ScriptedClient(replies), registry, self.run_dir).run(
            '请依次读取文件 target.txt 三次')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'invalid_protocol')
        self.assertEqual(len(registry.calls), 0)


if __name__ == '__main__':
    unittest.main()
