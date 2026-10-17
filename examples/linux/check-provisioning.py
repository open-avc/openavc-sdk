"""Exercise per-unit setup and credential replacement with the local example.

Starts only the staged example processes, never system services. The account
file below is a disposable fixture for the credential_check callback. A product
supplies that callback from its own existing account implementation.
"""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
from pathlib import Path
import pwd
import secrets
import selectors
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from stage import ROOT, parser, stage

from openavc_oem.manager import Manager
from product_checks import Client


def run_manager(configuration, accounts):
    def credentials(username, password):
        try:
            value = json.loads(accounts.read_text())
            return (value.get('enabled') is True
                    and hmac.compare_digest(username.encode(), value['username'].encode())
                    and hmac.compare_digest(password.encode(), value['password'].encode()))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
    asyncio.run(Manager(json.loads(configuration.read_text()), credential_check=credentials).run())


def check(args):
    evidence = args.out.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    evidence.chmod(0o755)
    args.out = evidence / 'product'
    if os.getuid() == 0 and args.service_user is None:
        args.service_user = 'nobody'
    temporary = tempfile.TemporaryDirectory(prefix='oavc-provision-')
    Path(temporary.name).chmod(0o755)
    if args.runtime_dir is None:
        args.runtime_dir = Path(temporary.name) / 'run'
    metadata = stage(args)
    account = pwd.getpwnam(metadata['service_user'])
    identity = {'user': account.pw_uid, 'group': account.pw_gid, 'extra_groups': []} if os.getuid() == 0 else {}
    private = args.out / 'private'
    configuration = private / 'openavc.json'
    config = json.loads(configuration.read_text())
    login = config.pop('login')
    accounts = private / 'product-credentials.json'
    unit_dir = private / 'unit'
    token_path = Path(config['management_token_file'])
    secrets_used = [login['password'], token_path.read_text()]
    processes, streams = [], []
    report = {'format': 1, 'status': 'failed', 'checks': [], 'staging': metadata,
              'hardware_observations': [], 'system_services_changed': False}
    environment = {**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                   'LD_LIBRARY_PATH': str(args.out / 'program/lib')}

    def own(path):
        if os.getuid() == 0:
            os.chown(path, account.pw_uid, account.pw_gid)

    def write(path, value):
        pending = path.with_suffix(path.suffix + '.new')
        pending.write_text(json.dumps(value, indent=2) + '\n')
        pending.chmod(0o600)
        own(pending)
        pending.replace(path)

    def command(argv):
        return subprocess.check_output(argv, cwd=ROOT, env=environment,
                                       stderr=subprocess.PIPE, timeout=30, **identity)

    def provision(*extra):
        raw = command([str(args.python), str(ROOT / 'provision-unit.py'), '--out', str(unit_dir), *extra])
        assert all(value.encode() not in raw for value in secrets_used)
        return json.loads(raw)

    def line(process):
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(20):
                raise ValueError('An example process did not respond. Read its local log.')
            return process.stdout.readline().decode().strip()

    def launch(name, argv, extra=None):
        stream = (evidence / (name + '.log')).open('w')
        streams.append(stream)
        process = subprocess.Popen(argv, cwd=ROOT, env={**environment, **(extra or {})},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stream, **identity)
        processes.append(process)
        assert line(process).startswith('READY'), 'Example startup failed; read its local log.'
        return process

    manager_number = 0
    def start_manager():
        nonlocal manager_number
        manager_number += 1
        return launch('management-' + str(manager_number), [str(args.python), str(Path(__file__).resolve()),
                      '--run-manager', str(configuration), str(accounts)])

    def stop(process):
        if process.poll() is None:
            process.terminate()
        output, _ = process.communicate(timeout=45)
        assert process.returncode == 0, 'Example shutdown failed; read its local log.'
        return output

    hardware = None
    def physical(expected):
        hardware.stdin.write(b'snapshot\n')
        hardware.stdin.flush()
        row = line(hardware).split()
        assert row[0] == 'STATE'
        actual = {'writes': int(row[1]), 'callbacks': int(row[2]), 'wrong_threads': int(row[3]),
                  'counter': int(row[5]), 'pending': int(row[13])}
        report['hardware_observations'].append(actual)
        assert actual == {'writes': expected, 'callbacks': expected, 'wrong_threads': 0,
                          'counter': expected, 'pending': 0}, 'Unexpected independent hardware effect.'

    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def management(token, payload=None, expected=200, path='/oem/settings'):
        request = urllib.request.Request('http://127.0.0.1:' + str(args.management_port) + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            response = http.open(request, timeout=50)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            assert response.status == expected, 'Unexpected management authorization result.'
            return json.load(response) if expected == 200 else None

    url = 'https://127.0.0.1:' + str(args.equipment_port)
    def client(credentials=login, ca=None):
        peer = Client(url, credentials, ca or config['ca_file'])
        peer.tls.verify_flags |= ssl.VERIFY_X509_STRICT
        return peer

    def increment(peer, expected=200):
        status, description = peer.request('/v1/description')
        assert status == 200
        assert description['identity'] == config['identity']
        status, result = peer.request('/v1/command', {'session': description['session'], 'id': 1,
            'command': 'increment', 'params': {}})
        assert status == expected, 'Unexpected equipment authorization result.'
        if expected == 200:
            assert result['outcome'] == 'completed'

    def refuse_command(peer, session):
        status, _ = peer.request('/v1/command', {'session': session, 'id': 2,
            'command': 'increment', 'params': {}})
        assert status == 401, 'A revoked product credential was accepted.'

    def refuse_tls(ca, expected):
        context = ssl.create_default_context(cafile=str(ca))
        try:
            with socket.create_connection(('127.0.0.1', args.equipment_port), timeout=5) as connection:
                with context.wrap_socket(connection, server_hostname='127.0.0.1'):
                    raise AssertionError('An invalid equipment certificate was accepted.')
        except ssl.SSLCertVerificationError as error:
            assert error.verify_code == expected, 'Certificate was refused for an unexpected reason.'
            report['checks'].append({'tls_refusal_code': error.verify_code})

    def certificates():
        directory = private / 'replacement-certificates'
        directory.mkdir(mode=0o700)
        own(directory)
        command(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '7',
            '-keyout', str(directory / 'ca.key'), '-out', str(directory / 'ca.pem'),
            '-subj', '/CN=Replacement example CA',
            '-addext', 'keyUsage=critical,keyCertSign,cRLSign'])
        command(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', str(directory / 'key.pem'), '-out', str(directory / 'request.pem'),
            '-subj', '/CN=Example product'])
        (directory / 'index').touch()
        (directory / 'serial').write_text('1000\n')
        (directory / 'ca.conf').write_text(f'''[ca]
default_ca = local
[local]
database = {directory}/index
new_certs_dir = {directory}
certificate = {directory}/ca.pem
private_key = {directory}/ca.key
serial = {directory}/serial
default_md = sha256
policy = subject
x509_extensions = server
unique_subject = no
[subject]
commonName = supplied
[server]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = IP:127.0.0.1,DNS:localhost
''')
        for path in directory.iterdir():
            own(path)
            path.chmod(0o600)
        now = datetime.now(timezone.utc)
        for name, start, end in [('valid', now-timedelta(minutes=1), now+timedelta(days=2)),
                                 ('expired', now-timedelta(days=2), now-timedelta(days=1)),
                                 ('future', now+timedelta(days=1), now+timedelta(days=2))]:
            command(['openssl', 'ca', '-batch', '-notext', '-config', str(directory / 'ca.conf'),
                '-startdate', start.strftime('%Y%m%d%H%M%SZ'), '-enddate', end.strftime('%Y%m%d%H%M%SZ'),
                '-in', str(directory / 'request.pem'), '-out', str(directory / (name + '.crt'))])
            pem = directory / (name + '.pem')
            pem.write_bytes((directory / (name + '.crt')).read_bytes() + (directory / 'key.pem').read_bytes())
            pem.chmod(0o600)
            own(pem)
        return directory

    try:
        report['step'] = 'unit_initialization'
        write(configuration, config)
        write(accounts, {**login, 'enabled': True})
        first = provision()
        repeated = provision('--identity', config['identity'])
        assert first['identity'] == repeated['identity'] == config['identity']
        assert first['created'] is repeated['created'] is False
        original_unit = (unit_dir / 'unit.json').read_bytes()
        other = private / 'another-unit'
        output = command([str(args.python), str(ROOT / 'provision-unit.py'), '--out', str(other)])
        assert json.loads(output)['identity'] != config['identity']
        assert (other / 'management.token').read_bytes() != token_path.read_bytes()
        marker = args.out / 'data/product-settings.json'
        write(marker, {'example_setting': 'retain-across-credential-replacement'})
        marker_bytes = marker.read_bytes()
        report['checks'].append('distinct_units_and_repeat_startup')

        hardware = launch('hardware', [str(args.out / 'program/hardware'), config['model'], '-'],
                          {'OPENAVC_PRODUCT_SOCKET': config['bridge_path']})
        report['step'] = 'product_credentials'
        manager = start_manager()
        peer = client()
        increment(peer)
        physical(1)
        session = peer.request('/v1/description')[1]['session']
        rotated = {'username': login['username'], 'password': secrets.token_urlsafe(24)}
        secrets_used.append(rotated['password'])
        write(accounts, {**rotated, 'enabled': True})
        refuse_command(peer, session)
        physical(1)
        increment(client(rotated))
        physical(2)
        write(accounts, {**rotated, 'enabled': False})
        refuse_command(client(rotated), session)
        physical(2)
        write(accounts, {**rotated, 'enabled': True})
        report['checks'].append('product_account_rotation_and_revocation_without_restart')

        report['step'] = 'management_credential'
        stop(manager)
        old_token = token_path.read_text()
        provision('--rotate-management-token')
        token = token_path.read_text()
        secrets_used.append(token)
        assert token != old_token
        manager = start_manager()
        management(old_token, expected=401)
        management(token)
        increment(client(rotated))
        physical(3)
        report['checks'].append('management_credential_replacement_with_hardware_running')

        report['step'] = 'certificate_replacement'
        stop(manager)
        old_ca = config['ca_file']
        issued = certificates()
        config.update(certificate=str(issued / 'valid.pem'), ca_file=str(issued / 'ca.pem'))
        write(configuration, config)
        manager = start_manager()
        refuse_tls(old_ca, 20)
        increment(client(rotated))
        physical(4)
        report['checks'].append('equipment_certificate_and_trust_replacement')
        for name, expected in [('expired', 10), ('future', 9)]:
            report['step'] = name + '_certificate'
            stop(manager)
            config['certificate'] = str(issued / (name + '.pem'))
            write(configuration, config)
            manager = start_manager()
            refuse_tls(config['ca_file'], expected)
            physical(4)
        stop(manager)
        config['certificate'] = str(issued / 'valid.pem')
        write(configuration, config)
        manager = start_manager()
        increment(client(rotated))
        physical(5)
        report['checks'].append('invalid_validity_refused_and_recovered_without_clock_change')

        if args.with_server:
            report['step'] = 'product_hosted_server'
            state = management(token)
            management(token, {'revision': state['revision'], 'field': 'server', 'enabled': True})
            programmer = {'username': 'programmer', 'password': secrets.token_urlsafe(24)}
            secrets_used.append(programmer['password'])
            basic = base64.b64encode((programmer['username'] + ':' + programmer['password']).encode()).decode()
            def server(path, payload=None):
                request = urllib.request.Request('http://127.0.0.1:' + str(args.server_port) + path,
                    data=json.dumps(payload).encode() if payload is not None else None,
                    headers={'Authorization': 'Basic ' + basic, 'Content-Type': 'application/json'})
                with http.open(request, timeout=30) as response:
                    return json.load(response)
            server('/api/auth/setup', programmer)
            deadline = time.monotonic() + 20
            while not any(device['id'] == 'local' and device['connected'] for device in server('/api/devices')['devices']):
                assert time.monotonic() < deadline, 'Product hardware did not reconnect.'
                time.sleep(.25)
            server('/api/devices/local/command', {'command': 'increment', 'params': {}})
            physical(6)
            assert client(rotated).request('/v1/description')[0] == 403
            report['checks'].append('product_hardware_uses_replaced_credentials_and_trust')

        report['step'] = 'support_export'
        status = management(token, path='/oem/diagnostics')
        status_path = evidence / 'product-status.json'
        status_path.write_text(json.dumps(status, indent=2) + '\n')
        output = command([str(args.python), str(ROOT / 'collect-diagnostics.py'), '--status', str(status_path),
                          '--out', str(args.out / 'data/support.json')])
        for value in secrets_used:
            assert value not in status_path.read_text() and value.encode() not in output
        assert b'PRIVATE KEY' not in output
        assert (unit_dir / 'unit.json').read_bytes() == original_unit
        assert marker.read_bytes() == marker_bytes
        report['checks'].append('support_export_omits_secrets_and_identity_data_preserved')
        stop(manager)
        closed = stop(hardware)
        (evidence / 'hardware-shutdown.txt').write_bytes(closed)
        assert b'CLOSED' in closed
        report['status'] = 'passed'
        report['step'] = 'complete'
    except BaseException as error:
        report['error_type'] = type(error).__name__
        cause = error
        while cause is not None:
            if isinstance(cause, ssl.SSLCertVerificationError):
                report['certificate_verify_code'] = cause.verify_code
                break
            cause = cause.__cause__ or cause.__context__
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=45)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
        for stream in streams:
            stream.close()
        temporary.cleanup()
        text = json.dumps(report, indent=2) + '\n'
        assert all(value not in text for value in secrets_used)
        (evidence / 'report.json').write_text(text)
    return report


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--run-manager':
        run_manager(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        command = parser()
        command.description = __doc__
        arguments = command.parse_args()
        try:
            result = check(arguments)
        except (ValueError, AssertionError, OSError, subprocess.SubprocessError) as error:
            command.exit(1, 'Provisioning check failed. Read report.json and local logs in the selected output directory.\n')
        print('Provisioning checks passed: ' + str(arguments.out.parent / 'report.json'))
