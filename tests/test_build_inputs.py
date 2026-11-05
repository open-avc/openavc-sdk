"""Refuse incompatible packages before generation, and retain failed-build evidence."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from packaging.version import Version
from tools.check_server_lock import check_sources, check_wheels, environment, read_lock, require
from tools.build_support import build_image, run_logged

ROOT = Path(__file__).resolve().parents[1]


class ServerLock(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wheel(self, name, version, requirements=(), tag='py3-none-any'):
        path = self.root / f'{name}-{version}-{tag}.whl'
        with ZipFile(path, 'w') as z:
            z.writestr(f'{name}-{version}.dist-info/METADATA',
                       f'Metadata-Version: 2.4\nName: {name}\nVersion: {version}\nRequires-Python: >=3.12\n'
                       + ''.join('Requires-Dist: ' + r + '\n' for r in requirements))
            z.writestr(name + '/__init__.py', 'raise RuntimeError("must not execute")')
        return {'version': Version(version), 'hashes': {hashlib.sha256(path.read_bytes()).hexdigest()}}

    def test_ranges_markers_missing_dependencies_and_direct_urls(self):
        pins = {'demo': {'version': Version('2.3')}}
        self.assertEqual(require('Demo>=2,<3', pins, 'core', environment('aarch64')), ('demo', set()))
        self.assertIsNone(require('absent; sys_platform == "win32"', pins, 'core', environment('aarch64')))
        for spec in ('demo==2.4', 'absent>=1', 'demo @ https://example.invalid/demo.whl'):
            with self.subTest(spec=spec), self.assertRaisesRegex(ValueError, 'core requires'):
                require(spec, pins, 'core', environment('aarch64'))
        with self.assertRaisesRegex(ValueError, 'absent'):
            require('absent; platform_machine == "x86_64"', pins, 'core', environment('x86_64'))

    def test_transitive_extras_are_checked_until_the_graph_settles(self):
        pins = {'parent': self.wheel('parent', '1', ['child[tls]>=1']),
                'child': self.wheel('child', '1', ['leaf>=2; extra == "tls"']),
                'leaf': self.wheel('leaf', '1')}
        with self.assertRaisesRegex(ValueError, 'child requires leaf'):
            check_wheels(self.root, pins, [('core', 'parent==1')], 'aarch64')
        (self.root / 'leaf-1-py3-none-any.whl').unlink()
        pins['leaf'] = self.wheel('leaf', '2')
        self.assertEqual(check_wheels(self.root, pins, [('core', 'parent==1')], 'aarch64'), 3)

    def test_wrong_hash_missing_wheel_and_wrong_processor_are_refused(self):
        pins = {'demo': self.wheel('demo', '1', tag='cp312-cp312-manylinux_2_17_x86_64')}
        with self.assertRaisesRegex(ValueError, 'does not support Linux aarch64'):
            check_wheels(self.root, pins, [], 'aarch64')
        self.assertEqual(check_wheels(self.root, pins, [], 'x86_64'), 1)
        pins['demo']['hashes'] = {'0' * 64}
        with self.assertRaisesRegex(ValueError, 'identity/hash'):
            check_wheels(self.root, pins, [], 'x86_64')
        next(self.root.glob('*.whl')).unlink()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            check_wheels(self.root, pins, [], 'x86_64')

    def test_malformed_or_duplicate_locks_are_not_accepted(self):
        path = self.root / 'lock.txt'
        for content in ('', 'demo>=1 --hash=sha256:' + '0' * 64,
                        'demo==1', 'demo==1 --hash=sha256:' + '0' * 64 + '\n' +
                        'Demo==2 --hash=sha256:' + '1' * 64):
            path.write_text(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                read_lock(path)

    def fixture(self, version='1', notice_version='1'):
        core = self.root / 'core'
        core.mkdir()
        subprocess.run(['git', 'init', '-q', str(core)], check=True)
        (core / 'pyproject.toml').write_text('[project]\ndependencies=["demo==' + version + '"]\n')
        subprocess.run(['git', '-C', str(core), 'add', 'pyproject.toml'], check=True)
        subprocess.run(['git', '-C', str(core), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', 'fixture'], check=True)
        sdk = self.root / 'sdk'
        (sdk / 'tools').mkdir(parents=True)
        (sdk / 'pyproject.toml').write_text('[project]\ndependencies=[]\n')
        (sdk / 'tools/requirements-server.txt').write_text('demo==1 --hash=sha256:' + '0' * 64 + '\n')
        (sdk / 'tools/wheel-notice-sources.json').write_text(json.dumps({'sources': [{'applies_to': [
            {'package': 'demo', 'version': notice_version, 'wheel_sha256': ['0' * 64]}]}]}))
        return core, sdk

    def test_stale_notice_versions_are_refused(self):
        core, sdk = self.fixture(notice_version='0')
        with self.assertRaisesRegex(ValueError, 'Wheel notices'):
            check_sources(core, 'HEAD', sdk)

    def test_incompatible_sync_leaves_pin_and_generated_files_untouched(self):
        core, sdk = self.fixture(version='2')
        for name in ('sync_contract.py', 'check_server_lock.py'):
            shutil.copy2(ROOT / 'tools' / name, sdk / 'tools' / name)
        generated = sdk / 'sdk/contract'
        generated.mkdir(parents=True)
        (generated / 'origin.json').write_text('{"core_head":"unchanged"}')
        (generated / 'payload.py').write_text('original bytes\n')
        before = {p.name: p.read_bytes() for p in generated.iterdir()}
        result = subprocess.run([sys.executable, str(sdk / 'tools/sync_contract.py'), '--core', str(core)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OpenAVC requires demo==2', result.stderr)
        self.assertEqual(before, {p.name: p.read_bytes() for p in generated.iterdir()})


class BuildEvidence(unittest.TestCase):
    def test_failed_command_prints_the_cause_and_keeps_the_full_log(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'build.log'
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(subprocess.CalledProcessError):
                run_logged([sys.executable, '-c', 'print("download: HTTP 503"); raise SystemExit(7)'],
                           path, 'Image preparation')
            self.assertIn('HTTP 503', stderr.getvalue())
            self.assertIn('Image preparation failed', stderr.getvalue())
            self.assertIn('HTTP 503', path.read_text())

    def test_failed_build_keeps_previous_cache_and_is_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / 'cross-x86_64'
            old.mkdir()
            (old / 'index.json').write_text('original')
            with patch.dict(os.environ, {'OPENAVC_BUILD_CACHE': folder}), \
                 patch('tools.build_support.run_logged', side_effect=subprocess.CalledProcessError(1, 'docker')) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    build_image(['docker'], ['.'], root / 'build.log', 'cross-x86_64')
            run.assert_called_once()
            self.assertEqual((old / 'index.json').read_text(), 'original')
            self.assertEqual(list(root.iterdir()), [old])


if __name__ == '__main__':
    unittest.main()
