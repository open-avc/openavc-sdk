"""Measure a separate process and prove support exports exclude private fields."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from diagnostic_report import summarize
from resource_profile import profile
from profile_comparison import compare


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
                                      '--label', 'firmware-a', '--workload', 'held memory',
                                      '--artifact', str(data / 'example'),
                                      '--out', str(self.work / 'profile')], capture_output=True, text=True, timeout=5)
        self.assertEqual(measurement.returncode, 0, measurement.stdout + measurement.stderr)
        report = json.loads((self.work / 'profile/report.json').read_text())
        self.assertEqual(report['status'], 'complete')
        self.assertGreaterEqual(report['samples'], 2)
        self.assertGreater(report['peak']['rss_bytes'], 4000000)
        self.assertEqual(report['last']['storage'], {'bytes': 19, 'files': 1, 'complete': True})
        self.assertEqual(report['context']['artifacts'], [{'name': 'example', 'bytes': 19,
                         'sha256': hashlib.sha256(b'x' * 19).hexdigest()}])
        self.assertEqual(report['context']['workload'], 'held memory')
        args = [sys.executable, str(ROOT / 'collect-diagnostics.py'), '--resources', str(self.work / 'profile/report.json')]
        preview = subprocess.run(args, capture_output=True, text=True, timeout=5)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        export = subprocess.run([*args, '--out', str(self.work / 'support.json')], capture_output=True, text=True, timeout=5)
        self.assertEqual(export.returncode, 0, export.stderr)
        self.assertEqual(preview.stdout, export.stdout)
        self.assertEqual(preview.stdout, (self.work / 'support.json').read_text())
        self.assertNotIn('root_processes', preview.stdout)
        self.assertNotIn('firmware-a', preview.stdout)
        comparison = subprocess.run([sys.executable, str(ROOT / 'compare-profiles.py'),
                                     '--baseline', str(self.work / 'profile/report.json'),
                                     '--candidate', str(self.work / 'profile/report.json'),
                                     '--out', str(self.work / 'comparison.json')],
                                    capture_output=True, text=True, timeout=5)
        self.assertEqual(comparison.returncode, 0, comparison.stderr)
        compared = json.loads(comparison.stdout)
        self.assertEqual(compared, json.loads((self.work / 'comparison.json').read_text()))
        self.assertEqual(compared['metrics']['rss_bytes']['difference'], 0)
        self.assertTrue(any('same report' in note for note in compared['notes']))

    def test_comparison_keeps_differences_and_unavailable_values_distinct(self):
        original = {'format': 1, 'status': 'complete',
                    'coverage': 'selected_local_processes_and_observed_descendants',
                    'cpu_percent_basis': '100_percent_is_one_logical_cpu',
                    'memory_basis': 'sum_of_RSS_may_count_shared_pages_more_than_once',
                    'host': {'system': 'Linux', 'machine': 'aarch64'},
                    'duration_requested_seconds': 60, 'elapsed_seconds': 60.1,
                    'interval_seconds': 1, 'samples': 61,
                    'peak': {'rss_bytes': 100, 'cpu_percent': 0, 'threads': 2},
                    'initial_storage': {'bytes': 100, 'complete': True},
                    'last': {'storage': {'bytes': 130, 'complete': True}}}
        candidate = json.loads(json.dumps(original))
        candidate['peak'].update(rss_bytes=125, cpu_percent=5, threads=1)
        candidate['host']['machine'] = 'x86_64'
        candidate['duration_requested_seconds'] = 120
        candidate['last']['storage']['complete'] = False
        baseline_path, candidate_path = self.work / 'baseline.json', self.work / 'candidate.json'
        baseline_path.write_text(json.dumps(original))
        candidate_path.write_text(json.dumps(candidate))
        result = compare(baseline_path, candidate_path)
        self.assertEqual(result['metrics']['rss_bytes'], {'baseline': 100, 'candidate': 125,
                                                         'difference': 25, 'change_percent': 25})
        self.assertEqual(result['metrics']['cpu_percent']['difference'], 5)
        self.assertIsNone(result['metrics']['cpu_percent']['change_percent'])
        self.assertEqual(result['metrics']['threads']['difference'], -1)
        self.assertIsNone(result['metrics']['descriptors']['difference'])
        self.assertEqual(result['metrics']['storage_growth_bytes']['baseline'], 30)
        self.assertIsNone(result['metrics']['storage_growth_bytes']['candidate'])
        self.assertIn('Reported host machine differs.', result['notes'])
        self.assertIn('Measurement duration_requested_seconds differs.', result['notes'])
        for change in ({'status': 'failed'}, {'cpu_percent_basis': 'whole_machine'}, {'peak': {'rss_bytes': float('nan')}}):
            candidate_path.write_text(json.dumps({**candidate, **change}))
            with self.assertRaises(ValueError):
                compare(baseline_path, candidate_path)

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
