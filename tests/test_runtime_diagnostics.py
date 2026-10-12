"""Exercise the public collector against log bytes and the host network APIs."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from diagnostic_report import summarize
from runtime_diagnostics import health_history, network_snapshot


ROOT = Path(__file__).resolve().parents[1]


class RuntimeDiagnostics(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.work = Path(self.folder.name)

    def test_health_bytes_keep_failure_timing_and_refuse_free_text(self):
        secret = 'PRIVATE_LOG_SENTINEL'
        path = self.work / secret
        record = {'event': 'core_health', 'start_unix_time': 1700000000,
                  'end_unix_time': 1700000001, 'duration_s': 1, 'interval_s': 1.01,
                  'timeout_s': 1, 'status': None, 'responsive': False,
                  'previous_responsive': True, 'error_type': 'TimeoutError',
                  'error': secret, 'identity': secret, 'password': secret}
        raw = (json.dumps(record) + '\n' + json.dumps({'event': secret}) + '\n' +
               'not JSON ' + secret + '\n' + '{"event":"core_health"').encode()
        path.write_bytes(raw)
        report = summarize(health_logs=[path])
        self.assertNotIn(secret, json.dumps(report))
        self.assertTrue(report['findings'])
        history = report['health']
        self.assertEqual(history['sources'], [{'source': 1, 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(), 'health_records': 1,
            'other_records': 1, 'unreadable_records': 1, 'incomplete_final_record': True}])
        self.assertEqual(history['retained_events'], [{'source': 1, 'record': 1,
            'start_unix_time': 1700000000, 'end_unix_time': 1700000001,
            'duration_s': 1, 'interval_s': 1.01, 'timeout_s': 1,
            'http_status': None, 'responsive': False, 'previous_responsive': True,
            'error_type': 'TimeoutError'}])
        record.update(responsive=secret, duration_s=secret, status=True, error_type=secret)
        path.write_text(json.dumps(record) + '\n')
        event = health_history([path])['retained_events'][0]
        self.assertIsNone(event['responsive'])
        self.assertIsNone(event['duration_s'])
        self.assertIsNone(event['http_status'])
        self.assertEqual(event['error_type'], 'unknown')

    def test_recent_events_are_bounded_and_segments_keep_selected_order(self):
        older, newer = self.work / 'health.jsonl.1', self.work / 'health.jsonl'
        older.write_text(''.join(json.dumps({'event': 'core_health', 'start_unix_time': n}) + '\n'
                                 for n in range(105)))
        newer.write_text('{"event":"core_health","start_unix_time":200}\n')
        history = health_history([older, newer])
        self.assertEqual(len(history['retained_events']), 100)
        self.assertEqual(history['omitted_older_events'], 6)
        self.assertEqual(history['retained_events'][0]['start_unix_time'], 6)
        self.assertEqual(history['retained_events'][-1]['source'], 2)
        self.assertEqual(history['retained_events'][-1]['start_unix_time'], 200)
        with self.assertRaises(ValueError):
            health_history([older] * 9)
        with older.open('wb') as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            health_history([older])

    def test_literal_preview_and_export_match_and_existing_output_is_preserved(self):
        path = self.work / 'health.jsonl'
        path.write_text('{"event":"core_health","status":503,"responsive":false}\n')
        args = [sys.executable, str(ROOT / 'collect-diagnostics.py'), '--health-log', str(path)]
        preview = subprocess.run(args, capture_output=True, text=True, timeout=5)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        destination = self.work / 'support.json'
        export = subprocess.run([*args, '--out', str(destination)], capture_output=True, text=True, timeout=5)
        self.assertEqual(export.returncode, 0, export.stderr)
        self.assertEqual(preview.stdout, destination.read_text())
        self.assertEqual(preview.stdout, export.stdout)
        refused = subprocess.run([*args, '--out', str(destination)], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(preview.stdout, destination.read_text())
        if hasattr(os, 'mkfifo'):
            fifo = self.work / 'fifo'
            os.mkfifo(fifo)
            result = subprocess.run([*args[:-1], str(fifo)], capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)

    def test_network_summary_excludes_names_addresses_and_unknown_statistics(self):
        secret = 'PRIVATE_INTERFACE_SENTINEL'
        addresses = {secret: [SimpleNamespace(family=socket.AF_INET6, address='fe80::1234%secret'),
                              SimpleNamespace(family=socket.AF_INET, address='127.0.0.1')]}
        statistics = {secret: SimpleNamespace(isup=True, mtu=1500, speed=0, flags='up,multicast'),
                      'no-addresses': SimpleNamespace(isup=False, mtu=0, speed=0)}
        with patch('psutil.net_if_addrs', return_value=addresses), patch('psutil.net_if_stats', return_value=statistics):
            report = network_snapshot()
        serialized = json.dumps(report)
        for value in (secret, 'fe80', '127.0.0.1', 'no-addresses'):
            self.assertNotIn(value, serialized)
        row = report['interfaces'][0]
        self.assertEqual(row['address_counts'], {'ipv4': 1, 'ipv6': 1, 'loopback': 1, 'link_local': 1})
        self.assertTrue(row['link_up'])
        self.assertTrue(row['multicast_capable'])
        self.assertIsNone(row['speed_mbps'])
        self.assertFalse(report['interfaces'][1]['addresses_available'])
        self.assertIsNone(report['interfaces'][1]['multicast_capable'])

    def test_literal_network_command_observes_the_current_host(self):
        result = subprocess.run([sys.executable, str(ROOT / 'collect-diagnostics.py'), '--network'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        network = json.loads(result.stdout)['network']
        self.assertEqual(network['coverage'], 'collector_host_interfaces')
        self.assertFalse(network['connectivity_verified'])
        self.assertTrue(network['interfaces'])
        self.assertTrue(any(row['address_counts']['loopback'] for row in network['interfaces']))
        if sys.platform in ('darwin', 'linux'):
            self.assertEqual(len(network['interfaces']), len(socket.if_nameindex()))


if __name__ == '__main__':
    unittest.main()
