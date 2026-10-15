"""Run a copied native build with no compiler; refuse changed output before launch."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TransferredBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='openavc-transfer-')
        cls.addClassCleanup(cls.folder.cleanup)
        cls.work = Path(cls.folder.name)
        cls.original = cls.work / 'original'
        result = subprocess.run([sys.executable, str(ROOT / 'try-example.py'), '--form', 'direct',
                                 '--build-dir', str(cls.original), '--build-only'],
                                capture_output=True, text=True, timeout=300)
        if result.returncode:
            log = cls.original / 'c-direct-build.log'
            raise RuntimeError(result.stdout + result.stderr + (log.read_text() if log.exists() else ''))
        tools = cls.work / 'tools'
        tools.mkdir()
        (tools / 'openssl').symlink_to(shutil.which('openssl'))
        cls.environment = {**os.environ, 'PATH': str(tools)}

    def copy_build(self, name):
        destination = self.work / name
        destination.mkdir()
        record = self.original / 'c-direct-build-result.json'
        for relative in json.loads(record.read_text())['files']:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.original / relative, target)
        shutil.copy2(record, destination / record.name)
        return destination

    def run_build(self, build):
        return subprocess.run([sys.executable, str(ROOT / 'try-example.py'), '--form', 'direct',
                               '--run-built', str(build), '--exercise-demo'],
                              env=self.environment, capture_output=True, text=True, timeout=30)

    def test_copied_build_runs_without_compiler(self):
        build = self.copy_build('valid')
        before = (build / 'c-direct-build-result.json').read_bytes()
        result = self.run_build(build)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((build / 'c-direct-result.json').read_text())
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(report['transferred_build'])
        self.assertEqual(report['hardware']['writes'], 3)
        self.assertEqual(set(report['interrupted_commands']), {'before_acceptance', 'before_response'})
        for case in report['interrupted_commands'].values():
            self.assertEqual(case['report']['status'], 'passed')
        self.assertEqual(before, (build / 'c-direct-build-result.json').read_bytes())

    def test_changed_binary_refused_before_launch(self):
        build = self.copy_build('changed')
        with (build / 'example-c').open('ab') as stream:
            stream.write(b'changed')
        result = self.run_build(build)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Transferred build changed: example-c', result.stderr)
        self.assertFalse((build / 'c-direct-endpoint.log').exists())


if __name__ == '__main__':
    unittest.main()
