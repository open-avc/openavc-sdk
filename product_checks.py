"""Workstation checks for a manufacturer's running equipment integration.

The check plan selects every physical operation. The runner never invents a
command, follows a redirect or resumes an interrupted run. Ordinary checks repeat
the ID after completion to check deduplication; interruption checks never replay.
"""
import base64
from datetime import datetime, timezone
import hashlib
import http.client
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


class ConnectionUnavailable(CheckFailed):
    """A request could not reach the equipment or obtain its response."""


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
        self.tls = ssl.create_default_context(cafile=ca_file)
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=self.tls))

    def interrupt_command(self, body, interruption):
        """Send once over verified TLS, then close without reading a response.

        A complete send establishes neither acceptance nor completion. The
        caller must inspect the retained result and feedback without replay.
        """
        require(interruption in ('before_acceptance', 'before_response'), 'Unknown interruption check.')
        url = urllib.parse.urlsplit(self.base + '/v1/command')
        raw = encoded(body)
        sent = raw[:-1] if interruption == 'before_acceptance' else raw
        connection = http.client.HTTPSConnection(url.hostname, url.port, timeout=self.timeout, context=self.tls)
        start = time.monotonic()
        try:
            connection.putrequest('POST', url.path)
            connection.putheader('Authorization', self.authorization)
            connection.putheader('X-OpenAVC-Client', self.identifier)
            connection.putheader('Content-Type', 'application/json')
            connection.putheader('Content-Length', str(len(raw)))
            connection.putheader('Connection', 'close')
            connection.endheaders()
            connection.send(sent)
            return {'body_bytes_sent': len(sent), 'content_length': len(raw), 'response_read': False}
        except (OSError, http.client.HTTPException) as error:
            raise CheckFailed('Could not complete the selected connection interruption; '
                              'acceptance is unknown. Inspect the hardware and do not replay the command.') from error
        finally:
            connection.close()
            self.timings.append({'method': 'POST', 'path': '/v1/command', 'interruption': interruption,
                                 'seconds': time.monotonic() - start})

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
            failure = CheckFailed if isinstance(getattr(error, 'reason', error), ssl.SSLCertVerificationError) else ConnectionUnavailable
            raise failure('Equipment connection failed. Check address, TLS trust and connectivity; '
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
        require(type(case) is dict and set(case) <= {'name', 'command', 'params', 'expect', 'outcome', 'timeout', 'observe_for', 'check_refusals', 'interruption'}
                and {'name', 'command', 'params', 'expect'} <= set(case), 'Check fields are invalid.')
        interruption = case.get('interruption')
        require(interruption in (None, 'before_acceptance', 'before_response'), 'Select a supported interruption.')
        require(type(case.get('check_refusals', False)) is bool, 'check_refusals must be true or false.')
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
        if interruption == 'before_acceptance':
            require('outcome' not in case, 'Omit outcome for an incomplete request; it must not be accepted.')
        else:
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


def run_checks(client, model, output, plan=None, *, reconnect=None, ready=None):
    """Run once; save intent before every POST and retain evidence on failure."""
    require(type(model) is dict and type(model.get('id')) is str, 'Supply the generated model.json from your build.')
    cases = prepare_cases(plan, model) if plan is not None else []
    if reconnect is not None:
        require(type(reconnect) in (int, float) and math.isfinite(reconnect) and 1 <= reconnect <= 3600,
                'Reconnect observation must be between 1 and 3600 seconds.')
        require(not cases, 'Run reconnect observation separately from command checks.')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {'format': 1, 'status': 'running', 'protocol': PROTOCOL,
              'started_at': datetime.now(timezone.utc).isoformat(),
              'model_canonical_sha256': hashlib.sha256(encoded(model)).hexdigest(),
              'plan_canonical_sha256': hashlib.sha256(encoded(plan)).hexdigest() if plan else None,
              'client_id': client.identifier, 'checks': [], 'commands': [], 'refusals': [],
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
    def check_refusals(case, body, before):
        unknown_command = '__openavc_undeclared_command'
        while unknown_command in model.get('commands', {}):
            unknown_command += '_'
        unknown_param = '__openavc_undeclared_parameter'
        while unknown_param in model['commands'][case['command']].get('params', {}):
            unknown_param += '_'
        requests = [
            ('authentication', body, False, (401, 403), None),
            ('changed_session', {**body, 'session': description['session'] + '-invalid'}, True, (409,), 'session_changed'),
            ('invalid_id', {**body, 'id': 0}, True, (422,), 'invalid_request'),
            ('unknown_command', {**body, 'command': unknown_command}, True, (422,), 'unknown_command'),
            ('unknown_parameter', {**body, 'params': {**body['params'], unknown_param: 0}}, True, (422,), 'invalid_parameter'),
        ]
        expected = [expectation_value(before, item) for item in case['expect']]
        for name, request, authenticated, statuses, code in requests:
            entry = {'check': case['name'], 'kind': name, 'request': request,
                     'authenticated': authenticated, 'expected_statuses': statuses,
                     'expected_error': code, 'status': 'dispatching'}
            report['refusals'].append(entry)
            save()
            status, result = client.request('/v1/command', request, authenticated=authenticated)
            entry.update(http_status=status, response=result, status='observing')
            save()
            require(status in statuses and (code is None or type(result) is dict and result.get('error') == code),
                    'Invalid command was not refused as expected (' + name + '). Stop and inspect the hardware.')
            deadline = time.monotonic() + case.get('observe_for', .5)
            while True:
                current = readback()
                entry['observed'] = [expectation_value(current, item) for item in case['expect']]
                require(entry['observed'] == expected,
                        'Reported state changed during a refused request (' + name + '). Stop and inspect the hardware.')
                if time.monotonic() >= deadline:
                    break
                time.sleep(max(0, min(.05, deadline - time.monotonic())))
            entry.update(status='passed', observed=expected)
            save()
        passed(case['name'] + ': invalid requests refused')
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
        if reconnect is not None:
            observation = {'status': 'waiting_for_disconnect', 'timeout': reconnect,
                           'initial_session': description['session']}
            report['reconnect'] = observation
            save()
            if ready:
                ready()
            started = time.monotonic()
            deadline = started + reconnect
            unavailable = False
            while True:
                require(time.monotonic() < deadline,
                        'Equipment did not recover within the observation window.' if unavailable
                        else 'No unavailable response was observed. Repeat with a longer interruption.')
                try:
                    status, candidate = client.request('/v1/description')
                    if status == 503:
                        raise ConnectionUnavailable('Equipment reported unavailable.')
                    require(status == 200 and type(candidate) is dict,
                            f'Description request returned HTTP {status} during reconnect observation.')
                    require(candidate.get('identity') == description['identity'],
                            'A different product answered after connection loss. Check the address and product identity.')
                    require(candidate.get('protocol') == PROTOCOL and candidate.get('capabilities') == model,
                            'Equipment protocol or capabilities changed. Use the matching SDK and generated model.')
                    require(type(candidate.get('session')) is str and candidate['session'], 'Equipment session is missing.')
                    status, candidate_state = client.request('/v1/snapshot')
                    if status == 503:
                        raise ConnectionUnavailable('Hardware readback reported unavailable.')
                    require(status == 200, f'Readback request returned HTTP {status} during reconnect observation.')
                    candidate_state = snapshot(candidate_state, candidate)
                    if unavailable:
                        description = candidate
                        previous = None
                        current = readback(candidate_state)
                        status, _ = client.request('/v1/description', authenticated=False)
                        require(status in (401, 403), 'Recovered equipment is accessible without authentication.')
                        query = urllib.parse.urlencode({'generation': current['generation'], 'revision': current['revision']})
                        status, changed = client.request('/v1/changes?' + query)
                        require(status == 200, f'Recovered change request returned HTTP {status}.')
                        current = readback(changed)
                        observation.update(status='passed', recovered_after_seconds=time.monotonic() - started,
                                           final_session=candidate['session'])
                        passed('same_product_recovered_without_commands')
                        break
                    current = readback(candidate_state)
                except ConnectionUnavailable:
                    if not unavailable:
                        unavailable = True
                        observation.update(status='waiting_for_recovery', unavailable_after_seconds=time.monotonic() - started)
                        save()
                time.sleep(min(.25, max(0, deadline - time.monotonic())))
        for identifier, case in enumerate(cases, 1):
            before = readback()
            interruption = case.get('interruption')
            targets = [item.get('equals') if 'equals' in item
                       else expectation_value(before, item) + item['delta'] for item in case['expect']]
            # Resolve every selected channel before admitting any command.
            for item in case['expect']:
                expectation_value(before, item)
            if interruption == 'before_acceptance':
                require(targets == [expectation_value(before, item) for item in case['expect']],
                        'An incomplete request must expect unchanged state. Use delta: 0 or the current value.')
            body = {'session': description['session'], 'id': identifier,
                    'command': case['command'], 'params': case['params']}
            if case.get('check_refusals', False):
                check_refusals(case, body, before)
            entry = {'name': case['name'], 'request': body, 'status': 'dispatching'}
            if interruption:
                entry['interruption'] = interruption
            report['commands'].append(entry)
            save()
            if interruption:
                entry['transport'] = client.interrupt_command(body, interruption)
                entry['status'] = 'observing'
                save()
                deadline = time.monotonic() + (case.get('observe_for', .5) if interruption == 'before_acceptance'
                                              else case.get('timeout', 10))
                while True:
                    current = readback()
                    status, result = client.request('/v1/commands/' + str(identifier))
                    if interruption == 'before_response' and status == 200:
                        break
                    require(status == 404 and type(result) is dict and result.get('error') == 'unknown_request',
                            'An incomplete request was accepted.' if interruption == 'before_acceptance'
                            else 'Could not establish command acceptance; do not replay it.')
                    if interruption == 'before_acceptance':
                        actual = [expectation_value(current, item) for item in case['expect']]
                        require(actual == targets, 'Reported state changed after an incomplete request.')
                        if time.monotonic() >= deadline:
                            break
                    else:
                        require(time.monotonic() < deadline,
                                'Command acceptance is unknown; inspect hardware and do not replay it.')
                    time.sleep(max(0, min(.05, deadline - time.monotonic())))
                if interruption == 'before_acceptance':
                    entry.update(status='passed', outcome='not_accepted', observed=actual)
                    passed(case['name'])
                    continue
            else:
                status, result = client.request('/v1/command', body)
            polled = bool(interruption)
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
                if interruption:
                    observations = entry.setdefault('observed_outcomes', [])
                    if not observations or observations[-1] != outcome:
                        observations.append(outcome)
                        save()
                if outcome not in ('pending', 'uncertain'):
                    break
                require(time.monotonic() < deadline, 'Hardware completion is still uncertain; do not replay the command.')
                time.sleep(.05)
                if interruption:
                    current = readback()
                status, result = client.request('/v1/commands/' + str(identifier))
                polled = True
            entry.update(outcome=outcome, status='observing')
            save()
            require(outcome == case.get('outcome', 'completed'), 'Command did not return its expected outcome.')
            for attempt in range(1 if interruption else 2):
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
                # Interrupted commands are observed without any duplicate POST.
                if attempt or interruption:
                    observation_end = time.monotonic() + case.get('observe_for', .5)
                    while True:
                        current = readback()
                        actual = [expectation_value(current, item) for item in case['expect']]
                        require(actual == targets, 'Reported state changed after command completion.')
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
