import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fusion_agent.contracts import ToolError
from fusion_agent.registry import ToolRegistry
from fusion_agent.web_fetch import WebFetchTools, validate_url, _public_addresses

HTML = b'<html><head><title>Projects</title></head><body><script>secret_script()</script><h1>Today</h1><article><a href="/owner/repo">owner/repo</a><p>Actual description</p></article></body></html>'


class WebFetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = WebFetchTools(self.root)
        self.registry = ToolRegistry(self.tools.specs(), allowed=('web',))

    def tearDown(self):
        self.tools.close()
        self.tmp.cleanup()

    def fetch(self, **kwargs):
        return self.registry.invoke('web.fetch', {'url': 'https://example.com/trending', **kwargs})

    def test_retrieve_parse_persist_readback(self):
        with patch.object(self.tools, '_get', return_value=(200, {'content-type':'text/html; charset=utf-8'}, HTML)):
            r = self.fetch()
        self.assertTrue(r['ok'], r)
        self.assertTrue(r['content_fetched'])
        self.assertIn('Actual description', r['text'])
        self.assertNotIn('secret_script', r['text'])
        self.assertEqual(r['links'][0]['url'], 'https://example.com/owner/repo')
        directory = Path(r['artifact'])
        self.assertEqual(set(p.name for p in directory.iterdir()), {'body.bin', 'text.txt', 'metadata.json'})
        self.assertEqual((directory/'body.bin').read_bytes(), HTML)

    def test_private_and_unsafe_targets_denied(self):
        for url in ('file:///etc/passwd', 'ftp://example.com', 'http://127.0.0.1', 'http://[::1]/',
                    'http://localhost/x', 'http://2130706433', 'http://0x7f000001',
                    'http://user:password@example.com', 'http://host.internal', 'http://example.com:8080',
                    'https://example.com\\@127.0.0.1', 'https://example.com/a\nB'):
            with self.subTest(url=url), self.assertRaises(ToolError):
                validate_url(url)

    def test_dns_private_or_mixed_answer_denied(self):
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
            with self.assertRaises(ToolError) as cm:
                _public_addresses('example.com', 80)
        self.assertEqual(cm.exception.code, 'private_url_denied')

    def test_public_dns(self):
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 80))]):
            self.assertEqual(_public_addresses('example.com', 80), ['93.184.216.34'])

    def test_redirect_target_is_validated_before_request(self):
        with patch.object(self.tools, '_get', return_value=(302, {'location': 'http://127.0.0.1/secrets'}, b'')) as get:
            r = self.fetch()
        self.assertFalse(r['ok'])
        self.assertEqual(get.call_count, 1)

    def test_redirect_read_follows_valid_target(self):
        with patch.object(self.tools, '_get', side_effect=[(302, {'location': '/final'}, b''),
                                                          (200, {'content-type': 'text/html'}, HTML)]):
            r = self.fetch()
        self.assertEqual(r['final_url'], 'https://example.com/final')
        self.assertEqual(r['url'], 'https://example.com/trending')

    def test_auth_http_failure_not_claimed_as_content(self):
        for status in (401, 403, 404, 500):
            with self.subTest(status=status), patch.object(self.tools, '_get', return_value=(status, {}, b'error')):
                r = self.fetch()
                self.assertFalse(r['ok'])
                self.assertNotIn('content_fetched', r)

    def test_page_challenge_never_bypassed_or_satisfied(self):
        with patch.object(self.tools, '_get', return_value=(200, {'content-type':'text/html'}, b'<title>Just a moment...</title>Verify you are human')):
            r = self.fetch()
        self.assertEqual(r['error']['code'], 'page_verification_required')

    def test_binary_rejected(self):
        with patch.object(self.tools, '_get', return_value=(200, {'content-type':'application/pdf'}, b'not a pdf')):
            r = self.fetch()
        self.assertEqual(r['error']['code'], 'unsupported_page_type')

    def test_large_text_paged_by_characters(self):
        body = ('abc\u4e2d' * 1000).encode('utf-8')
        with patch.object(self.tools, '_get', return_value=(200, {'content-type':'text/plain'}, body)):
            r = self.fetch(max_chars=101)
        self.assertTrue(r['truncated'])
        r2 = self.registry.invoke('web.read', {'page_id': r['page_id'], 'offset': r['next_offset'], 'max_chars': 200})
        self.assertEqual(r['text'] + r2['text'], body.decode()[:301])

    def test_no_arbitrary_path_read(self):
        r = self.registry.invoke('web.read', {'page_id': '/etc/passwd'})
        self.assertEqual(r['error']['code'], 'unknown_page')

    def test_changed_or_symlinked_page_is_rejected(self):
        with patch.object(self.tools, '_get', return_value=(200, {'content-type':'text/html'}, HTML)):
            r = self.fetch()
        path = Path(r['artifact'])/'text.txt'
        path.write_text('injected')
        self.assertEqual(self.registry.invoke('web.read', {'page_id':r['page_id']})['error']['code'], 'page_record_changed')
        path.unlink()
        outside = self.root/'other'
        outside.write_text('secret')
        path.symlink_to(outside)
        self.assertEqual(self.registry.invoke('web.read', {'page_id':r['page_id']})['error']['code'], 'page_record_changed')

    def test_metadata_only_retains_no_page_body_on_disk(self):
        self.tools.retain_content = False
        with patch.object(self.tools, '_get', return_value=(200, {'content-type':'text/html'}, HTML)):
            r = self.fetch()
        self.assertEqual(r['verification']['method'], 'http_get_and_memory_readback')
        self.assertEqual([p.name for p in Path(r['artifact']).iterdir()], ['metadata.json'])
        self.assertIn('Today', self.registry.invoke('web.read', {'page_id':r['page_id']})['text'])

    def test_no_authorization_no_network(self):
        self.registry.allowed.clear()
        with patch.object(self.tools, '_get', side_effect=AssertionError('must not run')):
            self.assertEqual(self.fetch()['error']['code'], 'capability_denied')

    def test_real_http_transport_with_local_fixture(self):
        # Exercise the actual sockets/http.client/body limits and disk handoff.
        # Only DNS+destination are substituted; no external site is contacted.
        import http.server
        import socket
        import threading
        seen = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                seen.append(self.path)
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(HTML)))
                self.end_headers()
                self.wfile.write(HTML)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original = socket.create_connection
        def connect(address, timeout=None, *args, **kwargs):
            return original(('127.0.0.1', server.server_port), timeout, *args, **kwargs)
        try:
            with patch('fusion_agent.web_fetch._public_addresses', return_value=['93.184.216.34']), \
                 patch('fusion_agent.web_fetch.socket.create_connection', side_effect=connect):
                result = self.fetch(url='http://example.com/trending', timeout=5)
            self.assertTrue(result['ok'], result)
            self.assertEqual(seen, ['/trending'])
            self.assertIn('Actual description', result['text'])
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
