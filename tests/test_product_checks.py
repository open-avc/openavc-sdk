"""Inject equipment faults and check evidence survives without command replay."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from product_checks import CheckFailed, ConnectionUnavailable, PROTOCOL, run_checks


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

    def test_refusal_option_is_checked_before_contacting_equipment(self):
        plan = copy.deepcopy(PLAN)
        plan['checks'][0]['check_refusals'] = 'true'
        equipment = FaultEquipment(None)
        with self.assertRaisesRegex(CheckFailed, 'check_refusals'):
            run_checks(equipment, MODEL, self.output, plan)
        self.assertEqual(equipment.posts, 0)
        self.assertEqual(equipment.reads, 0)

    def test_reconnect_is_read_only_and_requires_the_same_compatible_product(self):
        class RecoveringEquipment(FaultEquipment):
            watching = False
            interrupted = False

            def request(self, path, body=None, authenticated=True):
                if self.watching and authenticated and path == '/v1/description':
                    if not self.interrupted and self.fault != 'no_outage':
                        self.interrupted = True
                        if self.fault == 'socket_loss':
                            raise ConnectionUnavailable('Connection refused.')
                        return 503, {'error': 'unavailable'}
                    if self.fault == 'authorization':
                        return 403, {'error': 'forbidden'}
                status, value = super().request(path, body, authenticated)
                if self.interrupted and type(value) is dict:
                    value = copy.deepcopy(value)
                    value['session'] = 'restarted'
                    if 'generation' in value:
                        value.update(generation='new-generation', revision=0)
                    if self.fault == 'identity':
                        value['identity'] = 'replacement-unit'
                    elif self.fault == 'protocol':
                        value['protocol'] = 'unsupported'
                    elif self.fault == 'model' and 'capabilities' in value:
                        value['capabilities']['id'] = 'another-model'
                    elif self.fault == 'readback' and 'quality' in value:
                        value['quality'] = 'unknown'
                return status, value

        for fault in ('http_unavailable', 'socket_loss', 'identity', 'protocol', 'model', 'readback', 'authorization', 'no_outage'):
            with self.subTest(fault=fault):
                equipment = RecoveringEquipment(fault)
                output = self.output / fault
                def ready():
                    equipment.watching = True
                if fault in ('http_unavailable', 'socket_loss'):
                    result = run_checks(equipment, MODEL, output, reconnect=1, ready=ready)
                    self.assertEqual(result['reconnect']['final_session'], 'restarted')
                    self.assertEqual(result['status'], 'passed')
                else:
                    with self.assertRaises(CheckFailed):
                        run_checks(equipment, MODEL, output, reconnect=1, ready=ready)
                    self.assertEqual(json.loads((output / 'report.json').read_text())['status'], 'failed')
                self.assertEqual(equipment.posts, 0)

    def test_reconnect_cannot_run_a_command_plan(self):
        equipment = FaultEquipment(None)
        with self.assertRaisesRegex(CheckFailed, 'separately'):
            run_checks(equipment, MODEL, self.output, PLAN, reconnect=1)
        self.assertEqual(equipment.posts, 0)
        self.assertEqual(equipment.reads, 0)

    def test_refused_operations_precede_valid_work_and_preserve_evidence(self):
        class RefusingEquipment(FaultEquipment):
            def __init__(self, fault):
                super().__init__(fault)
                self.refusals = []
                self.extra_count = 0

            def request(self, path, body=None, authenticated=True):
                if body is not None:
                    error, status = None, None
                    if not authenticated:
                        error, status = 'unauthorized', 401
                    elif body['session'] != 'session':
                        error, status = 'session_changed', 409
                    elif body['id'] == 0:
                        error, status = 'invalid_request', 422
                    elif body['command'] != 'step':
                        error, status = 'unknown_command', 422
                    elif body['params']:
                        error, status = 'invalid_parameter', 422
                    if error:
                        self.refusals.append(copy.deepcopy(body))
                        if self.fault == 'lost_response':
                            raise CheckFailed('Response lost.')
                        if self.fault == 'writes_on_refusal':
                            self.extra_count += 1
                        if self.fault == 'wrong_reason':
                            return status, {'error': 'hardware_failed'}
                        return status, {'error': error}
                status, value = super().request(path, body, authenticated)
                if type(value) is dict and 'state' in value:
                    value['state']['count'] += self.extra_count
                return status, value

        plan = copy.deepcopy(PLAN)
        plan['checks'][0]['check_refusals'] = True
        for fault in (None, 'lost_response', 'writes_on_refusal', 'wrong_reason'):
            with self.subTest(fault=fault):
                equipment = RefusingEquipment(fault)
                output = self.output / str(fault)
                if fault:
                    with self.assertRaises(CheckFailed):
                        run_checks(equipment, MODEL, output, plan)
                    self.assertEqual(equipment.posts, 0)
                else:
                    run_checks(equipment, MODEL, output, plan)
                    self.assertEqual(equipment.posts, 2)
                    self.assertEqual(len(equipment.refusals), 5)
                report = json.loads((output / 'report.json').read_text())
                self.assertEqual(report['status'], 'failed' if fault else 'passed')
                self.assertEqual(len(report['refusals']), len(equipment.refusals))
                self.assertEqual(report['refusals'][0]['request'], equipment.refusals[0])
                if fault == 'writes_on_refusal':
                    self.assertEqual(report['refusals'][0]['observed'], [1])
                if not fault:
                    self.assertTrue(all(item['status'] == 'passed' for item in report['refusals']))


if __name__ == '__main__':
    unittest.main()
