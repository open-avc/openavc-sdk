"""Measure a separate process and prove support exports exclude private fields."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from diagnostic_report import summarize
from resource_profile import profile


ROOT = Path(__file__).resolve().parents[1]


class ProductReports(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.work = Path(self.folder.name)

    def test_measurement_and_literal_preview_export(self):
        process = subprocess.Popen([sys.executable, '-c', 'import sys; data=bytearray(4000000); print("READY", flush=True); sys.stdin.read()'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        def stop():
            process.communicate(timeout=5)
        self.addCleanup(stop)
        self.assertEqual(process.stdout.readline().strip(), 'READY')
        data = self.work / 'data'
        data.mkdir()
        (data / 'example').write_bytes(b'x' * 19)
        measurement = subprocess.run([sys.executable, str(ROOT / 'profile-product.py'), '--pid', str(process.pid),
                                      '--duration', '.1', '--interval', '.05', '--data-dir', str(data),
                                      '--out', str(self.work / 'profile')], capture_output=True, text=True, timeout=5)
        self.assertEqual(measurement.returncode, 0, measurement.stdout + measurement.stderr)
        report = json.loads((self.work / 'profile/report.json').read_text())
        self.assertEqual(report['status'], 'complete')
        self.assertGreaterEqual(report['samples'], 2)
        self.assertGreater(report['peak']['rss_bytes'], 4000000)
        self.assertEqual(report['last']['storage'], {'bytes': 19, 'files': 1, 'complete': True})
        args = [sys.executable, str(ROOT / 'collect-diagnostics.py'), '--resources', str(self.work / 'profile/report.json')]
        preview = subprocess.run(args, capture_output=True, text=True, timeout=5)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        export = subprocess.run([*args, '--out', str(self.work / 'support.json')], capture_output=True, text=True, timeout=5)
        self.assertEqual(export.returncode, 0, export.stderr)
        self.assertEqual(preview.stdout, export.stdout)
        self.assertEqual(preview.stdout, (self.work / 'support.json').read_text())
        self.assertNotIn('root_processes', preview.stdout)

    def test_exited_process_is_not_success_or_replaced_by_another_pid(self):
        process = subprocess.Popen([sys.executable, '-c', 'pass'])
        process.wait(timeout=5)
        with self.assertRaises(Exception):
            profile([process.pid], self.work / 'profile', duration=.1)

    def test_wrong_values_and_nested_secrets_cannot_enter_export(self):
        private = 'PRIVATE_PASSWORD_SENTINEL'
        result = summarize(status={'status': {'server': private, 'panel': True, 'error': private, 'identity': private,
                                              'equipment': {'reserved_locally': private}, 'extra': {'password': private}}},
                           checks={'format': 1, 'coverage': 'protocol_and_reported_state', 'status': private,
                                   'commands': [{'status': 'passed', 'request': {'password': private}}],
                                   'model_canonical_sha256': private, 'initial_snapshot': {'password': private}},
                           resources={'format': 1, 'coverage': 'selected_local_processes_and_observed_descendants',
                                      'status': private, 'peak': {'rss_bytes': private}, 'root_processes': {os.getpid(): private}})
        self.assertNotIn(private, json.dumps(result))
        self.assertIsNone(result['product']['server'])
        self.assertIs(result['product']['panel'], True)
        self.assertEqual(result['checks']['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
