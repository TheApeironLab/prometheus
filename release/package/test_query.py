"""Exercise the actual query HTTP handler, credentials and upstream boundaries."""
import http.client
import http.server
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.parse

SPEC = importlib.util.spec_from_file_location('query_server', Path(__file__).resolve().parents[2] / 'deploy/query-proxy/server.py')
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)


class QueryAccess(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.token = Path(self.temp.name) / 'token'
        self.token.write_text('a' * 40)
        self.requests = []
        requests = self.requests

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                requests.append((self.path, dict(self.headers)))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"success","data":{}}')

        upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        self.server = gateway.Server(('127.0.0.1', 0))
        self.server.token_file = self.token
        self.server.upstream = upstream.server_address
        for server in (upstream, self.server):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

    def request(self, path='/api/v1/query?query=up', token=None, method='GET', extra=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        headers.update(extra or {})
        try:
            conn.request(method, path, headers=headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_authentication_rotation_and_missing_secret_fail_closed(self):
        self.assertEqual(self.request()[0], 401)
        self.assertEqual(self.request(token='wrong')[0], 401)
        self.assertEqual(self.request(token='a'*40)[0], 200)
        self.token.write_text('b'*40)
        self.assertEqual(self.request(token='a'*40)[0], 401)
        self.assertEqual(self.request(token='b'*40)[0], 200)
        self.token.unlink()
        self.assertEqual(self.request(token='b'*40)[0], 503)
        self.assertEqual(len(self.requests), 2)

    def test_only_read_api_and_bounded_queries(self):
        for path in ('/-/reload', '/api/v1/admin/tsdb/delete_series', '/metrics',
                     '/api/v1/query/', '/api/v1/%71uery?query=up',
                     'http://other.invalid/api/v1/query?query=up'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, 'a'*40)[0], 403)
        for method in ('POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'):
            self.assertEqual(self.request(token='a'*40, method=method)[0], 405)
        for query in ('query=up&query=down', 'query=up&unknown=1', 'time=1'):
            self.assertEqual(self.request('/api/v1/query?' + query, 'a'*40)[0], 400)
        self.assertEqual(self.requests, [])

    def test_upstream_receives_no_credentials_or_cookie_and_timeout_is_capped(self):
        self.assertEqual(self.request('/api/v1/query?query=up&timeout=1h', 'a'*40,
                                     extra={'Cookie': 'private', 'X-Forwarded-Host': 'other'})[0], 200)
        path, headers = self.requests[0]
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['timeout'], ['5s'])
        self.assertNotIn('Authorization', headers)
        self.assertNotIn('Cookie', headers)
        self.assertNotIn('X-Forwarded-Host', headers)

    def test_oversized_response_is_rejected(self):
        previous = gateway.MAX_RESPONSE
        try:
            gateway.MAX_RESPONSE = 8
            self.assertEqual(self.request(token='a'*40)[0], 502)
        finally:
            gateway.MAX_RESPONSE = previous
