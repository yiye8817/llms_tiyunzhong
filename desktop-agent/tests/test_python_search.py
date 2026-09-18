from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fusion_agent.contracts import ToolError
from fusion_agent.local_tools import LocalTools


class PythonSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.local = LocalTools(self.root/'workspace', self.root/'runtime')
        original = self.local.shell_run
        def absent(args):
            if args['argv'][0] in ('rg', 'grep'):
                raise ToolError('command_not_found', 'test absence', not_executed=True)
            return original(args)
        self.mock = patch.object(self.local, 'shell_run', side_effect=absent)
        self.mock.start()

    def tearDown(self):
        self.mock.stop()
        self.local.close()
        self.tmp.cleanup()

    def test_both_absent_really_searches_python(self):
        (self.local.workspace/'a.txt').write_text('alpha\nBeta needle\nend')
        r = self.local.files_search({'query':'needle'})
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['backend'], 'python')
        self.assertEqual(r['matches'][0]['line'], 2)
        self.assertEqual([a['backend'] for a in r['attempts']], ['rg','grep','python'])

    def test_regex_unicode_and_case(self):
        (self.local.workspace/'a.txt').write_text('你好 WORLD 123')
        r = self.local.files_search({'query':'world [0-9]+', 'case_sensitive':False, 'fixed_strings':False})
        self.assertEqual(len(r['matches']), 1)

    def test_symlink_outside_not_followed(self):
        outside=self.root/'secret.txt';outside.write_text('private needle')
        (self.local.workspace/'link.txt').symlink_to(outside)
        r = self.local.files_search({'query':'needle'})
        self.assertEqual(r['matches'], [])

    def test_truncation_is_visible_and_bounded(self):
        (self.local.workspace/'a.txt').write_text(('needle '+ 'x'*1000+'\n')*100)
        r = self.local.files_search({'query':'needle','max_results':200})
        self.assertTrue(r['ok'],r)
        self.assertTrue(r['truncated'])
        self.assertLess(len(r['matches']), 100)

    def test_hostile_regex_is_stopped(self):
        (self.local.workspace/'a.txt').write_text('a'*10000+'!')
        r = self.local.files_search({'query':'(a+)+$', 'fixed_strings':False,'timeout':1})
        self.assertFalse(r['ok'])
        self.assertEqual(r['error']['code'], 'search_timeout')

    def test_bad_regex_returns_error_no_guessing(self):
        (self.local.workspace/'a.txt').write_text('hello')
        r = self.local.files_search({'query':'[', 'fixed_strings':False})
        self.assertFalse(r['ok'])
