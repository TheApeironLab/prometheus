"""Bounded, authenticated, read-only HTTPS access to the local Prometheus API."""
import hashlib
import hmac
import http.client
import http.server
import json
from pathlib import Path
import socketserver
import ssl
import threading
import urllib.parse

MAX_RESPONSE = 4 * 1024 * 1024
TOKEN_FILE = Path('/etc/query-auth/token')
ALLOWED = {
    '/api/v1/query': {'query', 'time', 'timeout'},
    '/api/v1/query_range': {'query', 'start', 'end', 'step', 'timeout'},
}


class Handler(http.server.BaseHTTPRequestHandler):
    # No request logging: URLs may contain sensitive selectors. Never return
    # authentication material, upstream diagnostics or client-supplied headers.
    def log_message(self, *_args):
        pass

    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def reply(self, status, body):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def error(self, status):
        self.reply(status, json.dumps({'error': http.server.BaseHTTPRequestHandler.responses[status][0]}).encode())

    def do_GET(self):
        try:
            # Read every request, including after a projected Secret rotation.
            with self.server.token_file.open('rb') as file:
                token = file.read(4097).strip()
            if not 32 <= len(token) <= 4096 or any(c < 33 or c > 126 for c in token):
                return self.error(503)
        except OSError:
            return self.error(503)
        supplied = self.headers.get_all('Authorization', [])
        if (len(supplied) != 1 or not hmac.compare_digest(
                hashlib.sha256(supplied[0].encode()).digest(),
                hashlib.sha256(b'Bearer ' + token).digest())):
            return self.error(401)
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0':
            return self.error(400)
        if len(self.path) > 16384:
            return self.error(414)
        target = urllib.parse.urlsplit(self.path)
        if target.scheme or target.netloc or target.fragment or target.path not in ALLOWED:
            return self.error(403)
        try:
            params = urllib.parse.parse_qs(target.query, max_num_fields=8, strict_parsing=True)
            if ('query' not in params or set(params) - ALLOWED[target.path]
                    or any(len(items) != 1 for items in params.values())):
                return self.error(400)
        except ValueError:
            return self.error(400)
        params['timeout'] = ['5s']
        # Fixed loopback origin; never follow redirects or forward user auth,
        # cookies, proxy environment, Host or other client headers upstream.
        upstream = http.client.HTTPConnection(*self.server.upstream, timeout=8)
        try:
            upstream.request('GET', target.path + '?' + urllib.parse.urlencode(params, doseq=True))
            response = upstream.getresponse()
            if response.status not in (200, 400, 422, 503):
                return self.error(502)
            body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                return self.error(502)
            json.loads(body)
            self.reply(response.status, body)
        except (OSError, ValueError, http.client.HTTPException):
            self.error(502)
        finally:
            upstream.close()

    def do_POST(self):
        self.error(405)

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_POST


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    request_queue_size = 8
    token_file = TOKEN_FILE
    upstream = ('127.0.0.1', 9090)

    def __init__(self, address, context=None):
        self.context = context
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(10)
        if self.context:
            # Handshake happens in the bounded worker, not the accept loop.
            conn = self.context.wrap_socket(conn, server_side=True, do_handshake_on_connect=False)
        return conn, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass


if __name__ == '__main__':
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain('/etc/query-tls/tls.crt', '/etc/query-tls/tls.key')
    Server(('0.0.0.0', 9443), tls).serve_forever()
