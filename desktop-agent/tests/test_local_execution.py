import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fusion_agent.contracts import ToolError
from fusion_agent.local_tools import LocalTools
from fusion_agent.local_execution import LocalExecutionTools, inventory
from fusion_agent.registry import ToolRegistry


class LocalExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.local = LocalTools(self.root / 'workspace', self.root / 'runtime')
        self.events = []
        self.exec = LocalExecutionTools(self.local, event=lambda n, f: self.events.append((n, f)))
        self.registry = ToolRegistry(self.exec.specs(), allowed=('shell', 'skills'))

    def tearDown(self):
        self.local.close()
        self.tmp.cleanup()

    def invoke(self, **kwargs):
        return self.registry.invoke('python.run', {'purpose': 'test authorized computation', **kwargs})

    def test_inventory_never_runs_cli_or_imports_optional_modules(self):
        with patch('subprocess.Popen', side_effect=AssertionError('must not execute')):
            result = inventory(['python3', 'fusion-nonexistent-program'])
        self.assertFalse(result['programs'][1]['available'])
        self.assertEqual(result['python_executable'], sys.executable)

    def test_inventory_rejects_shell_path_and_empty_names(self):
        for name in ('', '../python', '/bin/ls', 'bad\nname'):
            with self.subTest(name=name), self.assertRaises(ToolError):
                inventory([name])

    def test_real_native_is_preferred_and_invalid_unused_fallback_is_not_run(self):
        r = self.registry.invoke('local.run', {'purpose': 'print', 'argv': [sys.executable, '-c', 'print("native")'],
                                             'fallback_python': 'invalid syntax !!!'})
        self.assertTrue(r['ok'])
        self.assertEqual(r['backend'], 'local')
        self.assertEqual(r['stdout'].strip(), 'native')
        self.assertFalse(self.exec.artifact_dir.exists())

    def test_missing_command_really_runs_python_and_archives_files(self):
        r = self.registry.invoke('local.run', {'purpose': 'count tokens', 'argv': ['fusion-missing-998291'],
            'fallback_python': 'import json,sys\nv=json.load(open(sys.argv[1]))\nprint(len(v["text"].split()))',
            'input': {'text': 'one two three'}})
        self.assertTrue(r['ok'], r)
        self.assertTrue(r['fallback_used'])
        self.assertEqual(r['stdout'].strip(), '3')
        folder = Path(r['script']).parent
        self.assertEqual(set(p.name for p in folder.iterdir()), {'tool.py', 'input.json', 'manifest.json', 'result.json'})
        self.assertEqual((folder/'tool.py').stat().st_mode & 0o777, 0o600)
        manifest = json.loads((folder/'manifest.json').read_text())
        self.assertFalse(manifest['sandbox'])
        self.assertEqual(manifest['original_argv'], ['fusion-missing-998291'])
        self.assertEqual(r['python_executable'], sys.executable)

    def test_native_failure_or_timeout_never_replays_fallback(self):
        for code, timeout in [('raise SystemExit(2)', 5), ('import time;time.sleep(5)', 1)]:
            r = self.registry.invoke('local.run', {'purpose': 'test', 'argv': [sys.executable, '-c', code],
                                                 'timeout': timeout, 'fallback_python': 'open("bad", "w").write("bad")'})
            self.assertFalse(r['ok'])
            self.assertEqual(r['backend'], 'local')
            self.assertFalse((self.local.workspace/'bad').exists())

    def test_missing_script_is_not_missing_executable(self):
        r = self.registry.invoke('local.run', {'purpose': 'test', 'argv': [sys.executable, 'absent.py'],
                                             'fallback_python': 'print("wrong fallback")'})
        self.assertFalse(r['ok'])
        self.assertFalse(r['fallback_used'])

    def test_non_executable_has_no_fallback(self):
        program = self.local.workspace/'tool'
        program.write_text('print(1)')
        r = self.registry.invoke('local.run', {'purpose': 'test', 'argv': [str(program)],
                                             'fallback_python': 'print("wrong fallback")'})
        self.assertFalse(r['ok'])
        self.assertEqual(r['error']['code'], 'command_start_failed')
        self.assertEqual(r['execution']['status'], 'not_started')

    def test_shell_denial_blocks_code_and_artifacts(self):
        registry = ToolRegistry(self.exec.specs(), allowed=('skills',))
        r = registry.invoke('python.run', {'purpose': 'test', 'code': 'print(7)'})
        self.assertEqual(r['error']['code'], 'capability_denied')
        self.assertFalse(self.exec.artifact_dir.exists())

    def test_disabled_generation_still_runs_native(self):
        self.exec.enabled = False
        self.assertEqual(self.invoke(code='print(1)')['error']['code'], 'python_execution_disabled')
        r = self.registry.invoke('local.run', {'purpose': 'test', 'argv': [sys.executable, '-c', 'print(1)']})
        self.assertTrue(r['ok'])

    def test_invalid_syntax_does_not_launch(self):
        with patch.object(self.local, 'shell_run', side_effect=AssertionError('must not run')):
            r = self.invoke(code='print(')
        self.assertEqual(r['error']['code'], 'python_syntax_error')
        self.assertEqual(r['execution']['status'], 'not_started')

    def test_source_error_is_recorded_as_possible_side_effect(self):
        r = self.invoke(code='open("created.txt", "w").write("created")\nraise RuntimeError("failure")')
        self.assertFalse(r['ok'])
        self.assertTrue(r['outcome_unknown'])
        self.assertTrue((self.local.workspace/'created.txt').is_file())
        self.assertTrue((Path(r['script']).parent/'result.json').exists())

    def test_missing_dependency_no_pip_install(self):
        r = self.invoke(code='import fusion_definitely_absent_library_029')
        self.assertFalse(r['ok'])
        self.assertIn('dependency_hint', r)
        self.assertNotIn('pip install', r['stdout'])

    def test_metadata_only_removes_source_and_input(self):
        self.exec.retain_content = False
        r = self.invoke(code='print("private-payload")', input={'secret': 'sensitive'})
        self.assertTrue(r['ok'])
        directory = Path(r['script']).parent
        self.assertFalse((directory/'tool.py').exists())
        self.assertFalse((directory/'input.json').exists())
        self.assertNotIn('private-payload', (directory/'result.json').read_text())

    def test_source_parent_symlink_refused(self):
        destination = self.root/'outside'
        destination.mkdir()
        self.exec.artifact_dir.parent.mkdir(parents=True, exist_ok=True)
        self.exec.artifact_dir.symlink_to(destination, target_is_directory=True)
        r = self.invoke(code='print(7)')
        self.assertEqual(r['error']['code'], 'python_artifact_write_failed')
        self.assertEqual(list(destination.iterdir()), [])

    def test_cwd_outside_workspace_refused(self):
        r = self.invoke(code='print(7)', cwd='..')
        self.assertEqual(r['error']['code'], 'path_outside_workspace')
        self.assertEqual(r['execution']['status'], 'not_started')

    def test_python_argv_input_is_not_shell_expanded(self):
        r = self.invoke(code='import json,sys\nprint(json.load(open(sys.argv[1]))["x"])', input={'x': '$(touch bad)'})
        self.assertEqual(r['stdout'].strip(), '$(touch bad)')
        self.assertFalse((self.local.workspace/'bad').exists())
