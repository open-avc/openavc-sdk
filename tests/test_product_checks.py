"""Inject equipment faults and check evidence survives without command replay."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from product_checks import CheckFailed, PROTOCOL, run_checks


MODEL = {'id': 'test_counter', 'commands': {'step': {'params': {}}},
         'state_variables': {'count': {'type': 'integer'}}}
PLAN = {'model_id': 'test_counter', 'checks': [{'name': 'Step', 'command': 'step', 'params': {},
                                             'expect': [{'state': 'count', 'delta': 1}], 'observe_for': .1}]}


class FaultEquipment:
    identifier = 'a' * 32

    def __init__(self, fault):
        self.fault = fault
        self.posts = 0
        self.reads = 0
        self.timings = []

    def request(self, path, body=None, authenticated=True):
        if not authenticated:
            return 401, None
        if path == '/v1/description':
            return 200, {'identity': 'unit', 'session': 'session', 'protocol': PROTOCOL, 'capabilities': MODEL}
        if body is not None:
            self.posts += 1
            if self.fault == 'lost_response':
                raise CheckFailed('Connection closed after dispatch.')
            return (202 if self.fault == 'invalid_status' else 200), {'id': body['id'], 'outcome': 'completed'}
        self.reads += 1
        count = int(self.posts > 0)
        if self.posts > 1 and self.fault == 'duplicate_execution':
            count = 2
        return 200, {'identity': 'unit', 'session': 'changed' if self.posts and self.fault == 'restart' else 'session',
                     'generation': 'unit:1', 'revision': self.reads, 'quality': 'current',
                     'state': {'count': count}, 'children': []}


class ProductChecks(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.output = Path(self.folder.name) / 'evidence'

    def test_default_never_sends_hardware_commands(self):
        equipment = FaultEquipment(None)
        report = run_checks(equipment, MODEL, self.output)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(equipment.posts, 0)

    def test_entire_plan_is_checked_before_first_operation(self):
        plan = copy.deepcopy(PLAN)
        plan['checks'].append({**plan['checks'][0], 'params': {'invented': 1}})
        equipment = FaultEquipment(None)
        with self.assertRaisesRegex(CheckFailed, 'undeclared parameter'):
            run_checks(equipment, MODEL, self.output, plan)
        self.assertEqual(equipment.posts, 0)
        self.assertEqual(equipment.reads, 0)

    def test_failure_stops_dispatch_and_retains_original_intent(self):
        for fault in ('lost_response', 'invalid_status', 'restart'):
            with self.subTest(fault=fault):
                equipment = FaultEquipment(fault)
                output = self.output / fault
                with self.assertRaises(CheckFailed):
                    run_checks(equipment, MODEL, output, PLAN)
                report = json.loads((output / 'report.json').read_text())
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['commands'][0]['request']['id'], 1)
                self.assertEqual(equipment.posts, 1)
                with self.assertRaises(FileExistsError):
                    run_checks(equipment, MODEL, output, PLAN)
                self.assertEqual(equipment.posts, 1)

    def test_duplicate_execution_fails_readback(self):
        equipment = FaultEquipment('duplicate_execution')
        plan = copy.deepcopy(PLAN)
        plan['checks'][0]['timeout'] = .05
        with self.assertRaises(CheckFailed):
            run_checks(equipment, MODEL, self.output, plan)
        self.assertEqual(equipment.posts, 2)
        report = json.loads((self.output / 'report.json').read_text())
        self.assertEqual(report['status'], 'failed')

    def test_uncertain_completion_is_polled_before_duplicate_check(self):
        class DelayedEquipment(FaultEquipment):
            polls = 0

            def request(self, path, body=None, authenticated=True):
                if path.startswith('/v1/commands/'):
                    self.polls += 1
                    return 200, {'id': 1, 'outcome': 'pending' if self.polls == 1 else 'completed'}
                if body is not None:
                    self.posts += 1
                    if self.posts == 1:
                        return 202, {'id': 1, 'outcome': 'uncertain'}
                    if self.polls < 2:
                        raise AssertionError('Repeated a command before terminal completion')
                    return 200, {'id': 1, 'outcome': 'completed', 'duplicate': True}
                return super().request(path, body, authenticated)
        equipment = DelayedEquipment(None)
        report = run_checks(equipment, MODEL, self.output, PLAN)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(equipment.polls, 2)
        self.assertEqual(equipment.posts, 2)


if __name__ == '__main__':
    unittest.main()
