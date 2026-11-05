"""Exercise APT's configured retry bound and hash rejection on a local server."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

HELPER = Path('/usr/lib/apt/apt-helper')
CONFIG = Path(__file__).resolve().parents[1] / 'tools/apt-retries.conf'


@unittest.skipUnless(HELPER.is_file(), 'Debian APT is required for its download check')
class AptDownloads(unittest.TestCase):
    def test_retry_recovers_but_bad_hash_and_persistent_errors_still_fail(self):
        payload = b'controlled archive bytes\n'
        attempts = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                attempts[self.path] = attempts.get(self.path, 0) + 1
                if self.path == '/down' or (self.path == '/temporary' and attempts[self.path] == 1):
                    self.send_error(503)
                    return
                body = b'wrong bytes' if self.path == '/corrupt' else payload
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                for endpoint, success in (('temporary', True), ('corrupt', False), ('down', False)):
                    destination = Path(folder) / endpoint
                    # -c loads this file after host defaults; APT_CONFIG is
                    # read before them and can be overridden by the runner.
                    result = subprocess.run([str(HELPER), '-c', str(CONFIG), 'download-file',
                        f'http://127.0.0.1:{server.server_port}/{endpoint}', str(destination),
                        'SHA256:' + hashlib.sha256(payload).hexdigest()],
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
                    if success:
                        self.assertEqual(destination.read_bytes(), payload)
                self.assertEqual(attempts['/temporary'], 2)
                self.assertEqual(attempts['/down'], 4)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
