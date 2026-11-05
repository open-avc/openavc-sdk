"""Exercise the actual wget range behavior that can mix two mirror responses."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'examples/linux/yocto/fetch.py'


@unittest.skipUnless(shutil.which('wget'), 'wget is required for the mirror download check')
class MirrorDownload(unittest.TestCase):
    def test_rejected_html_is_not_prepended_to_the_next_mirror_archive(self):
        archive = bytes(range(256)) * 512
        html = b'<html>Download unavailable</html>'
        ranges = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = html if self.path == '/origin' else archive
                header = self.headers.get('Range')
                if header:
                    ranges.append(header)
                    offset = int(header.removeprefix('bytes=').split('-')[0])
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes {offset}-{len(body)-1}/{len(body)}')
                    body = body[offset:]
                else:
                    self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / 'archive.tmp'
                base = f'http://127.0.0.1:{server.server_port}'
                for prefix, expected in ((['wget'], False), ([sys.executable, str(SCRIPT)], True)):
                    output.unlink(missing_ok=True)
                    ranges.clear()
                    for endpoint in ('origin', 'mirror'):
                        subprocess.run([*prefix, '--tries=1', '--timeout=5', '--output-document=' + str(output),
                                        '--continue', base + '/' + endpoint], check=True,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.assertEqual(hashlib.sha256(output.read_bytes()).digest() == hashlib.sha256(archive).digest(), expected)
                    self.assertEqual(bool(ranges), not expected)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
