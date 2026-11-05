"""A package fallback must preserve snapshot selections and reject changed bytes."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import apt_packages


class PackageSelection(unittest.TestCase):
    def test_only_hashed_snapshot_archives_are_accepted(self):
        uri = ('https://snapshot.debian.org/archive/debian/20261012T203205Z/'
               'pool/main/m/make-dfsg/make_4.3-4.1_arm64.deb')
        line = f"'{uri}' make_4.3-4.1_arm64.deb 391184 SHA256:" + 'a' * 64
        selected, = apt_packages.package_uris('Reading package lists...\n' + line)
        self.assertEqual(selected['uri'], uri)
        self.assertEqual(apt_packages.mirror_uri(uri),
                         'https://deb.debian.org/debian/pool/main/m/make-dfsg/make_4.3-4.1_arm64.deb')
        for bad in (line.replace('SHA256:', 'MD5Sum:'),
                    line.replace(' 391184 ', ' 0 '),
                    line.replace("' make_", "' ../make_"),
                    line.replace('snapshot.debian.org', 'example.org'),
                    line + '\n' + line,
                    line.replace('/pool/', '/dists/')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                apt_packages.package_uris(bad)
        with self.assertRaises(ValueError):
            apt_packages.mirror_uri(uri.replace('pool/main/m/make-dfsg/make_4.3-4.1_arm64.deb',
                                               'dists/bookworm/InRelease'))


@unittest.skipUnless(Path(apt_packages.HELPER).is_file(), 'Debian APT is required')
class PackageDownloads(unittest.TestCase):
    def test_missing_mirror_falls_back_and_corrupt_archives_are_rejected(self):
        payload = b'verified package bytes\n'
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append(self.path)
                if self.path == '/mirror/missing.deb':
                    self.send_error(404)
                    return
                body = b'corrupted package bytes' if self.path.endswith('/corrupt.deb') else payload
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'

        def package(name):
            return {'uri': f'{base}/snapshot/{name}.deb', 'file': f'{name}.deb',
                    'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}

        try:
            with tempfile.TemporaryDirectory() as folder, patch.object(
                    apt_packages, 'mirror_uri', side_effect=lambda uri: uri.replace('/snapshot/', '/mirror/')):
                destination = Path(folder)
                records = apt_packages.fetch_packages([package('good'), package('missing')], destination)
                self.assertEqual({p.name for p in destination.iterdir()}, {'good.deb', 'missing.deb'})
                self.assertTrue(all(p.read_bytes() == payload for p in destination.iterdir()))
                self.assertEqual({r['download_uri'] for r in records},
                                 {f'{base}/mirror/good.deb', f'{base}/snapshot/missing.deb'})
                self.assertNotIn('/snapshot/good.deb', requests)
                with self.assertRaisesRegex(RuntimeError, 'corrupt.deb'):
                    apt_packages.fetch_packages([package('corrupt')], destination)
                self.assertFalse((destination / 'corrupt.deb').exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
