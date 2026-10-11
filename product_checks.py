"""Workstation checks for a manufacturer's running equipment integration.

The check plan selects every physical operation. The runner never invents a
command, follows a redirect or resumes an interrupted run. Each selected command
is deliberately repeated with the same ID after completion to check deduplication.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from openavc_oem._protocol import MAX_MESSAGE_BYTES, PROTOCOL


class CheckFailed(ValueError):
    """A reported contract, command or feedback check did not pass."""


def require(condition, message):
    if not condition:
        raise CheckFailed(message)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'JSON contains a duplicate field.')
        value[key] = item
    return value


def decode(raw):
    def invalid_number(value):
        raise CheckFailed('JSON contains a non-finite number.')
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_number)


def read_json(path, limit=MAX_MESSAGE_BYTES):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'Input exceeds the supported file size.')
    return decode(raw)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


class Client:
    def __init__(self, url, credentials, ca_file=None, timeout=5):
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == 'https' and parsed.hostname and not parsed.username
                and not parsed.password and not parsed.query and not parsed.fragment,
                'Use an HTTPS equipment URL without credentials, a query or a fragment.')
        require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
                'Use a positive request timeout.')
        require(type(credentials) is dict, 'Credentials must be a JSON object.')
        if set(credentials) == {'username', 'password'}:
            require(all(type(value) is str and value for value in credentials.values())
                    and ':' not in credentials['username'], 'Supply a username and password.')
            token = (credentials['username'] + ':' + credentials['password']).encode()
            self.authorization = 'Basic ' + base64.b64encode(token).decode()
        else:
            require(set(credentials) == {'token'} and type(credentials['token']) is str
                    and credentials['token'] and not any(c.isspace() for c in credentials['token']),
                    'Supply product username/password, or a local endpoint token.')
            self.authorization = 'Bearer ' + credentials['token']
        self.base = url.rstrip('/')
        self.timeout = timeout
        self.identifier = secrets.token_hex(16)
        self.timings = []
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def request(self, path, body=None, *, authenticated=True):
        headers = {'Content-Type': 'application/json', 'X-OpenAVC-Client': self.identifier}
        if authenticated:
            headers['Authorization'] = self.authorization
        request = urllib.request.Request(self.base + path, headers=headers,
                                         data=None if body is None else encoded(body))
        start = time.monotonic()
        try:
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.status
                raw = response.read(MAX_MESSAGE_BYTES + 1)
            require(len(raw) <= MAX_MESSAGE_BYTES, 'Equipment response exceeds the protocol message size.')
            # A refused unauthenticated request need not contain protocol JSON.
            if not authenticated and status in (401, 403):
                return status, None
            return status, decode(raw)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise CheckFailed('Equipment connection failed. Check address, TLS trust and connectivity; '
                              'an interrupted command must not be replayed.') from error
        finally:
            self.timings.append({'method': 'GET' if body is None else 'POST',
                                 'path': path.split('?')[0],
                                 'seconds': time.monotonic() - start})


def scalar(value, rule, location):
    kind = rule.get('type', 'string')
    valid = {'boolean': type(value) is bool, 'integer': type(value) is int,
             'number': type(value) in (int, float), 'float': type(value) in (int, float),
             'string': type(value) is str,
             'enum': type(value) in (str, int, float, bool)}
    require(valid.get(kind, False), location + ': value does not match its declared type.')
    if type(value) is str:
        require('\0' not in value, location + ': text contains a null character.')
    if type(value) in (int, float):
        require(math.isfinite(value), location + ': number is not finite.')
        require(('min' not in rule or value >= rule['min'])
                and ('max' not in rule or value <= rule['max']), location + ': outside declared range.')
    if 'values' in rule:
        choices = [item['value'] if type(item) is dict else item for item in rule['values']]
        require(any(value == item and (type(value) is bool) == (type(item) is bool)
                    for item in choices), location + ': not a declared enum value.')


def state_values(actual, rules, location):
    require(type(actual) is dict and set(actual) == set(rules), location + ': state fields differ from the model.')
    for key, rule in rules.items():
        scalar(actual[key], rule, location + '.' + key)


def channel_id(value, rule):
    scalar(value, {'type': 'integer', **rule.get('id_format', {})}, 'channel.id')
    if type(value) is str:
        require(re.fullmatch(r'[A-Za-z0-9_-]+', value) is not None, 'Channel ID contains unsupported characters.')


def snapshot(value, description):
    require(type(value) is dict and value.get('quality') == 'current', 'Current hardware feedback is unavailable.')
    require(value.get('identity') == description['identity'] and value.get('session') == description['session'],
            'Equipment identity or session changed. Stop and inspect the product before another command.')
    require(type(value.get('generation')) is str and value['generation']
            and type(value.get('revision')) is int and 0 <= value['revision'] <= 9007199254740991,
            'Readback generation or revision is invalid.')
    model = description['capabilities']
    state_values(value.get('state'), model.get('state_variables', {}), 'state')
    require(type(value.get('children')) is list, 'Readback must contain a channel list.')
    seen = set()
    for child in value['children']:
        require(type(child) is dict and type(child.get('type')) is str, 'Channel record is invalid.')
        rule = model.get('child_entity_types', {}).get(child['type'])
        require(rule is not None, 'Readback contains an undeclared channel type.')
        channel_id(child.get('id'), rule)
        key = (child['type'], type(child['id']), child['id'])
        require(key not in seen, 'Readback contains a duplicate channel.')
        seen.add(key)
        state_values(child.get('state'), rule.get('state_variables', {}), 'channel.state')
    return value


def get_snapshot(client, description):
    status, value = client.request('/v1/snapshot')
    require(status == 200, f'Readback request returned HTTP {status}.')
    return snapshot(value, description)


def expectation_value(value, expectation):
    if 'child_type' not in expectation:
        return value['state'][expectation['state']]
    children = [child for child in value['children']
                if child['type'] == expectation['child_type']
                and type(child['id']) is type(expectation['child_id'])
                and child['id'] == expectation['child_id']]
    require(len(children) == 1, 'A selected channel is unavailable.')
    return children[0]['state'][expectation['state']]


def prepare_cases(plan, model):
    require(type(plan) is dict and set(plan) == {'model_id', 'checks'}
            and plan['model_id'] == model['id'] and type(plan['checks']) is list,
            'Check file must contain this model_id and a checks list.')
    for case in plan['checks']:
        require(type(case) is dict and set(case) <= {'name', 'command', 'params', 'expect', 'outcome', 'timeout', 'observe_for'}
                and {'name', 'command', 'params', 'expect'} <= set(case), 'Check fields are invalid.')
        require(type(case['name']) is str and case['name'], 'Give each check a name.')
        require(type(case['command']) is str, 'Select a declared command name.')
        command = model.get('commands', {}).get(case['command'])
        require(command is not None and type(case['params']) is dict, 'Select a declared command and parameter object.')
        params = command.get('params', {})
        require(not set(case['params']) - set(params), 'Check has an undeclared parameter.')
        for key, rule in params.items():
            require(key in case['params'] or not rule.get('required'), 'Check is missing required parameter ' + key + '.')
            if key in case['params']:
                if rule.get('type') == 'child_id':
                    channel_id(case['params'][key], model['child_entity_types'][rule['child_type']])
                else:
                    scalar(case['params'][key], rule, 'params.' + key)
        require(case.get('outcome', 'completed') in ('completed', 'rejected', 'failed'), 'Select a terminal outcome.')
        timeout = case.get('timeout', 10)
        require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 300,
                'A check timeout must be greater than zero and at most 300 seconds.')
        observe_for = case.get('observe_for', .5)
        require(type(observe_for) in (int, float) and math.isfinite(observe_for) and .1 <= observe_for <= 30,
                'observe_for must be between 0.1 and 30 seconds.')
        require(type(case['expect']) is list and case['expect'], 'Each command check needs a readback expectation.')
        for expected in case['expect']:
            require(type(expected) is dict and set(expected) <= {'state', 'equals', 'delta', 'child_type', 'child_id'}
                    and 'state' in expected and (('equals' in expected) != ('delta' in expected)),
                    'Expectation needs state and exactly one of equals or delta.')
            require(('child_type' in expected) == ('child_id' in expected), 'Specify both child_type and child_id.')
            rules = model.get('state_variables', {})
            if 'child_type' in expected:
                require(type(expected['child_type']) is str, 'Select a declared channel type.')
                child = model.get('child_entity_types', {}).get(expected['child_type'])
                require(child is not None, 'Expectation names an undeclared channel type.')
                channel_id(expected['child_id'], child)
                rules = child.get('state_variables', {})
            require(type(expected['state']) is str and expected['state'] in rules, 'Expectation names undeclared state.')
            if 'equals' in expected:
                scalar(expected['equals'], rules[expected['state']], 'expected.equals')
            else:
                require(rules[expected['state']].get('type') in ('number', 'float', 'integer')
                        and type(expected['delta']) in (int, float) and math.isfinite(expected['delta']),
                        'Delta requires a finite number and numeric state.')
    return plan['checks']


def run_checks(client, model, output, plan=None):
    """Run once; save intent before every POST and retain evidence on failure."""
    require(type(model) is dict and type(model.get('id')) is str, 'Supply the generated model.json from your build.')
    cases = prepare_cases(plan, model) if plan is not None else []
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {'format': 1, 'status': 'running', 'protocol': PROTOCOL,
              'started_at': datetime.now(timezone.utc).isoformat(),
              'model_canonical_sha256': hashlib.sha256(encoded(model)).hexdigest(),
              'plan_canonical_sha256': hashlib.sha256(encoded(plan)).hexdigest() if plan else None,
              'client_id': client.identifier, 'checks': [], 'commands': [],
              'coverage': 'protocol_and_reported_state', 'physical_hardware_verified': False}
    def save():
        temporary = output / 'report.json.tmp'
        with temporary.open('w') as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(output / 'report.json')
    def passed(name):
        report['checks'].append(name)
        save()
    previous = None
    def readback(value=None):
        nonlocal previous
        value = get_snapshot(client, description) if value is None else snapshot(value, description)
        if previous is not None:
            require(value['generation'] == previous['generation'] and value['revision'] >= previous['revision'],
                    'Readback generation changed or revision moved backward.')
        previous = value
        return value
    try:
        save()
        status, description = client.request('/v1/description')
        require(status == 200 and type(description) is dict, f'Description request returned HTTP {status}.')
        require(description.get('protocol') == PROTOCOL, 'Equipment protocol is not supported by this SDK.')
        require(description.get('capabilities') == model, 'Equipment capabilities differ from the supplied generated model.')
        require(type(description.get('identity')) is str and description['identity']
                and type(description.get('session')) is str and description['session'], 'Equipment identity/session is missing.')
        report.update(identity=description['identity'], session=description['session'])
        current = readback()
        report['initial_snapshot'] = current
        passed('description_and_readback')
        status, _ = client.request('/v1/description', authenticated=False)
        require(status in (401, 403), 'Equipment description is accessible without authentication.')
        passed('authentication_required')
        query = urllib.parse.urlencode({'generation': current['generation'], 'revision': current['revision']})
        status, changed = client.request('/v1/changes?' + query)
        require(status == 200, f'Change request returned HTTP {status}.')
        current = readback(changed)
        passed('change_readback')
        for identifier, case in enumerate(cases, 1):
            before = readback()
            targets = [item.get('equals') if 'equals' in item
                       else expectation_value(before, item) + item['delta'] for item in case['expect']]
            # Resolve every selected channel before admitting any command.
            for item in case['expect']:
                expectation_value(before, item)
            body = {'session': description['session'], 'id': identifier,
                    'command': case['command'], 'params': case['params']}
            entry = {'name': case['name'], 'request': body, 'status': 'dispatching'}
            report['commands'].append(entry)
            save()
            status, result = client.request('/v1/command', body)
            polled = False
            deadline = time.monotonic() + case.get('timeout', 10)
            while True:
                require(status in (200, 202, 409) and type(result) is dict
                        and type(result.get('id')) is int and result['id'] == identifier,
                        f'Command returned an invalid result (HTTP {status}); do not replay it.')
                outcome = result.get('outcome')
                require(outcome in ('completed', 'rejected', 'failed', 'pending', 'uncertain'), 'Command outcome is invalid.')
                require(status == (200 if polled else {'completed': 200, 'rejected': 409, 'failed': 409,
                                                       'pending': 202, 'uncertain': 202}[outcome]),
                        'Command HTTP status does not match its outcome; do not replay it.')
                if outcome not in ('pending', 'uncertain'):
                    break
                require(time.monotonic() < deadline, 'Hardware completion is still uncertain; do not replay the command.')
                time.sleep(.05)
                status, result = client.request('/v1/commands/' + str(identifier))
                polled = True
            entry.update(outcome=outcome, status='observing')
            save()
            require(outcome == case.get('outcome', 'completed'), 'Command did not return its expected outcome.')
            for attempt in range(2):
                if attempt:
                    # Intentional duplicate-ID conformance check, only after terminal completion.
                    entry['status'] = 'checking_duplicate'
                    save()
                    repeated_status, repeated = client.request('/v1/command', body)
                    require(repeated_status == (200 if outcome == 'completed' else 409) and type(repeated) is dict
                            and {key: value for key, value in repeated.items() if key != 'duplicate'} == result,
                            'Duplicate request did not return the retained result.')
                feedback_deadline = time.monotonic() + case.get('timeout', 10)
                while True:
                    current = readback()
                    actual = [expectation_value(current, item) for item in case['expect']]
                    if actual == targets:
                        break
                    require(time.monotonic() < feedback_deadline, 'Reported hardware state did not reach the expected values.')
                    time.sleep(.05)
                # Read for a bounded interval after the duplicate, including at its end.
                if attempt:
                    observation_end = time.monotonic() + case.get('observe_for', .5)
                    while True:
                        current = readback()
                        actual = [expectation_value(current, item) for item in case['expect']]
                        require(actual == targets, 'Reported state changed after the duplicate request.')
                        if time.monotonic() >= observation_end:
                            break
                        time.sleep(max(0, min(.05, observation_end - time.monotonic())))
            entry.update(status='passed', observed=actual)
            passed(case['name'])
        report.update(status='passed', final_snapshot=current)
    except BaseException as error:
        report.update(status='failed', error=str(error) if isinstance(error, CheckFailed)
                      else 'Check failed while reading the integration or writing evidence. Inspect the local inputs and report.')
        raise
    finally:
        report['requests'] = client.timings
        save()
    return report
