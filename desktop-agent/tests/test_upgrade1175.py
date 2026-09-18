"""Regression fixtures for events3-style Python strings and both 1.17.4 configs."""
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest

from fusion_agent.config import initialize, load_settings, update_settings, migrate_1174_settings
from fusion_agent.runtime import parse_reply, ProtocolError
from fusion_agent.response_files import RepairArchive
from fusion_agent.repair_json import replay


def broken_python(code, *, tool='python.run', field=None, **extra):
    field = field or ('fallback_python' if tool == 'local.run' else 'code')
    action = {'type': 'action', 'tool': tool, 'arguments': {field: '__SOURCE__', **extra}, 'summary': '校验本地文件'}
    if tool == 'local.run':
        action['arguments']['argv'] = ['missing-fixture-tool']
    token = json.dumps(code, ensure_ascii=False).replace('\\"', '"')
    return json.dumps(action, ensure_ascii=False).replace('"__SOURCE__"', token)


class PythonQuote1175Tests(unittest.TestCase):
    def test_events3_pattern_roundtrip(self):
        # Same syntax as events3; private path/log content is not redistributed.
        code = '''import json
import sys

file_path = "/tmp/example/events.jsonl"

total_lines = 0
valid_lines = 0
errors = []
try:
    with open(file_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            total_lines += 1
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
                valid_lines += 1
            except json.JSONDecodeError as e:
                errors.append(f"Line {i}: {str(e)}")
except Exception as e:
    print(f"Error reading file: {e}")
    sys.exit(1)
print(f"Total non-empty lines: {total_lines}")
print(f"Valid JSON lines: {valid_lines}")
if errors:
    print(f"Errors found: {len(errors)}")
    for err in errors[:5]:
        print(err)
else:
    print("All lines are valid JSON.")
'''
        changes = []
        action = parse_reply(broken_python(code), changes)
        self.assertEqual(action['arguments']['code'], code)
        self.assertEqual(changes[0]['count'], 18)
        self.assertEqual(changes[0]['kind'], 'unescaped_python_quotes')
        self.assertEqual(changes[0]['python_source_sha256']['arguments.code'], hashlib.sha256(code.encode()).hexdigest())
    def test_dict_boundaries_are_not_json_members(self):
        code='d = {"name": "value", "next": {"a": "b"}}\nprint(d["name"])\n'
        self.assertEqual(parse_reply(broken_python(code))['arguments']['code'], code)
    def test_lists_and_sets(self):
        code='a = ["x", "y"]\nb = {"x", "y"}\nprint(a, b)\n'
        self.assertEqual(parse_reply(broken_python(code))['arguments']['code'], code)
    def test_f_strings(self):
        code='n = 1\nprint(f"rows: {n}")\n'
        self.assertEqual(parse_reply(broken_python(code))['arguments']['code'], code)
    def test_triple_quoted_multiline(self):
        code='s = """text with "quotes"\nand another line"""\nprint(s)\n'
        self.assertEqual(parse_reply(broken_python(code))['arguments']['code'], code)
    def test_literal_backslashes_and_unicode(self):
        code='path = r"C:\\logs\\中文"\npattern = r"\\d+"\nprint(path, pattern)\n'
        self.assertEqual(parse_reply(broken_python(code))['arguments']['code'], code)
    def test_mixed_raw_newlines(self):
        code='print("first")\nprint("second")\n'
        raw=broken_python(code).replace('\\n','\n')
        changes=[]
        self.assertEqual(parse_reply(raw,changes)['arguments']['code'],code)
        self.assertIn('raw_json_string_newlines',[c['kind'] for c in changes])
    def test_local_fallback(self):
        code='print("fallback")\n'
        self.assertEqual(parse_reply(broken_python(code,tool='local.run'))['arguments']['fallback_python'],code)
    def test_tool_selector_after_arguments(self):
        raw=broken_python('print("x")\n')
        raw=raw.replace('"tool": "python.run", ','').replace('"summary":','"tool": "python.run", "summary":')
        self.assertEqual(parse_reply(raw)['tool'],'python.run')
    def test_already_valid_json_never_rewritten(self):
        action={'type':'action','tool':'python.run','arguments':{'code':'print("x")\n'},'summary':'s'}
        changes=[]
        self.assertEqual(parse_reply(json.dumps(action),changes),action)
        self.assertFalse(changes)
    def test_valid_json_invalid_python_not_rewritten(self):
        action={'type':'action','tool':'python.run','arguments':{'code':'not valid python ???'},'summary':'s'}
        self.assertEqual(parse_reply(json.dumps(action)),action)
    def test_invalid_python_is_not_guessed_by_quote_repair(self):
        for code in ('print("x"\n','return "x"\n','if True print("x")\n'):
            with self.subTest(code=code),self.assertRaises(ProtocolError):
                parse_reply(broken_python(code))
    def test_truncated_action_stops(self):
        with self.assertRaises(ProtocolError):parse_reply(broken_python('print("x")\n')[:-12])
    def test_duplicate_keys_stop(self):
        raw=broken_python('print("x")\n').replace('"arguments": {','"arguments": {"code":"pass",')
        with self.assertRaises(ProtocolError):parse_reply(raw)
    def test_unknown_tool_and_shell_strings_not_repaired(self):
        with self.assertRaises(ProtocolError):
            parse_reply(broken_python('print("x")\n', tool='unknown.tool', field='code'))
        repaired = parse_reply(broken_python('echo "x"', tool='shell.run', field='command'))
        self.assertEqual(repaired['arguments']['command'], 'echo "x"')
    def test_multiple_objects_stop(self):
        with self.assertRaises(ProtocolError):parse_reply(broken_python('print("x")\n')+' {"type":"final","answer":"extra"}')
    def test_repair_never_executes_code(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'must-not-exist'
            code='from pathlib import Path\nPath("'+str(target)+'").write_text("side effect")\n'
            parse_reply(broken_python(code))
            self.assertFalse(target.exists())
    def test_diagnostic_source_and_metadata_only(self):
        with tempfile.TemporaryDirectory() as d:
            raw=broken_python('print("secret-fixture")\n');changes=[]
            action=parse_reply(raw,changes)
            info=RepairArchive(Path(d)/'full').save(raw,action=action,normalizations=changes)
            path=Path(info['python_source'])
            self.assertEqual(path.read_text(),action['arguments']['code'])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            report=json.loads(Path(info['report']).read_text())
            self.assertFalse(report['python_source']['executed'])
            private=RepairArchive(Path(d)/'private',content=False).save(raw,action=action,normalizations=changes)
            self.assertNotIn('python_source',private)
            self.assertNotIn('secret-fixture',Path(private['report']).read_text())
    def test_offline_replay(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'raw.txt';p.write_text(broken_python('print("check")\n'))
            result=replay(p,Path(d)/'out')
            self.assertEqual(result['passed'],1)
            self.assertEqual(result['algorithm'],'python_deterministic_v6')
            self.assertEqual(result['executed_actions'],0)
            self.assertEqual(result['server_requests'],0)



class FileHandoffPython1175Tests(unittest.TestCase):
    def _run(self, allow_shell):
        from fusion_agent.local_tools import LocalTools
        from fusion_agent.local_execution import LocalExecutionTools
        from fusion_agent.registry import ToolRegistry
        from fusion_agent.response_files import ResponseStore
        from fusion_agent.runtime import Runtime
        from test_runtime import ScriptedClient, final
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            local = LocalTools(root / 'workspace', root / 'runtime')
            try:
                source = local.workspace / 'fixture.jsonl'
                source.write_text('{"row":1}\n{"row":2}\n', encoding='utf-8')
                code = ('import json\nfrom pathlib import Path\n'
                        'p = Path("' + str(source) + '")\n'
                        'print(sum(1 for line in p.read_text().splitlines() if json.loads(line)))\n')
                events = []
                emit = lambda n, f: events.append((n, f))
                store = ResponseStore(root / 'responses', event=emit)
                ref = store.put(broken_python(code, purpose='Count test-only JSON lines'))
                execution = LocalExecutionTools(local)
                registry = ToolRegistry([*local.specs(), *execution.specs()],
                                        allowed=('files', 'shell') if allow_shell else ('files',))
                client = ScriptedClient([ref, final('2')])
                runtime = Runtime(client, registry, root / 'run', event=emit, response_store=store)
                result = runtime.run('用 Python 校验测试文件中的 JSON 行数')
                self.assertEqual(sum(n == 'agent.response_file_read' for n, _ in events), 1)
                if allow_shell:
                    self.assertEqual(result['status'], 'completed', result)
                    self.assertEqual(result['steps'], 1)
                    names = [n for n, _ in events]
                    self.assertLess(names.index('agent.response_file_read'), names.index('tool.attempt'))
                    outputs = list(execution.artifact_dir.rglob('result.json'))
                    self.assertEqual(len(outputs), 1)
                    self.assertIn('2', outputs[0].read_text())
                    scripts = list(execution.artifact_dir.rglob('tool.py'))
                    self.assertEqual(len(scripts), 1)
                    self.assertEqual(scripts[0].read_text(), code)
                else:
                    self.assertNotEqual(result['status'], 'completed', result)
                    self.assertFalse(execution.artifact_dir.exists())
            finally:
                local.close()

    def test_repaired_file_handoff_runs_only_authorized_test_fixture_once(self):
        self._run(True)

    def test_transport_repair_never_grants_shell(self):
        self._run(False)


class ConfigMigration1175Tests(unittest.TestCase):
    def test_old_schema_load_and_save(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'custom.json'
            old={'file_access':'unrestricted','persist_settings':True,'strict_json_protocol':True,
                 'workspace':'relative-work','model':'glm'}
            p.write_text(json.dumps(old));original=p.read_bytes()
            s=load_settings(p)
            self.assertEqual(s.filesystem_scope,'host');self.assertTrue(s.auto_save_config)
            self.assertEqual(p.read_bytes(),original,'load never rewrites configuration')
            update_settings(s,{'timeout':120})
            saved=json.loads(p.read_text())
            self.assertNotIn('file_access',saved);self.assertNotIn('strict_json_protocol',saved)
            self.assertEqual(saved['workspace'],'relative-work')
            self.assertEqual(load_settings(p).model,'glm')
            self.assertEqual(load_settings(p).timeout,120)
    def test_restricted_old_scope_preserved(self):
        self.assertEqual(migrate_1174_settings({'file_access':'workspace'})['filesystem_scope'],'workspace')
    def test_disabled_persistence_preserved(self):
        self.assertFalse(migrate_1174_settings({'persist_settings':False})['auto_save_config'])
    def test_conflicting_aliases_fail(self):
        for v in ({'file_access':'workspace','filesystem_scope':'host'},
                  {'persist_settings':False,'auto_save_config':True}):
            with self.subTest(v=v),self.assertRaises(ValueError):migrate_1174_settings(v)
    def test_alias_type_validation(self):
        for v in ({'file_access':[]},{'persist_settings':'yes'},{'strict_json_protocol':0}):
            with self.subTest(v=v),self.assertRaises(ValueError):migrate_1174_settings(v)
    def test_new_schema_unchanged(self):
        value={'filesystem_scope':'host','auto_save_config':False,'model':'qwen'}
        self.assertEqual(migrate_1174_settings(value),value)
    def test_unknown_fields_still_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';p.write_text('{"typo":true}')
            with self.assertRaises(ValueError):load_settings(p)

if __name__=='__main__':unittest.main()
