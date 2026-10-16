"""Exercise product controls with the native adapter and an installed OpenAVC."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest

from aiohttp import BasicAuth, ClientSession, ClientTimeout
from openavc_oem._protocol import MAX_MESSAGE_BYTES
from product_checks import Client

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


@unittest.skipUnless(sys.platform == 'linux' and (ROOT / 'build/openavc-device-service').exists(),
                     'Build the Linux service before running the complete host check')
class HostSystem(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oavc-host-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.processes, self.logs = [], []
        self.addAsyncCleanup(self.shutdown)
        self.http = ClientSession(timeout=ClientTimeout(total=60))
        self.addAsyncCleanup(self.http.close)
        self.login = {'username': 'engineer', 'password': secrets.token_urlsafe(24)}
        self.management_token = secrets.token_hex(32)
        (self.root / 'management.token').write_text(self.management_token)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-keyout', str(self.root / 'key.pem'), '-out', str(self.root / 'ca.pem'),
                        '-subj', '/CN=Example equipment', '-addext', 'subjectAltName=IP:127.0.0.1,IP:::1'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (self.root / 'server.pem').write_bytes((self.root / 'ca.pem').read_bytes() + (self.root / 'key.pem').read_bytes())
        self.tls = ssl.create_default_context(cafile=str(self.root / 'ca.pem'))
        model = str(ROOT / 'build/generated/model.json')
        self.product = await self.launch('hardware', [str(ROOT / 'build/example-c'), model, '-'],
            env={**os.environ, 'OPENAVC_PRODUCT_SOCKET': str(self.root / 'product.sock')})
        self.assertEqual(await self.line(self.product), 'READY 0')
        equipment, management, server = free_port(), free_port(), free_port()
        self.config = {'identity': 'acme-host-test', 'data_dir': str(self.root / 'data'),
            'model': model, 'management_token_file': str(self.root / 'management.token'),
            'device_service': str(ROOT / 'build/openavc-device-service'), 'bridge_path': str(self.root / 'product.sock'),
            'certificate': str(self.root / 'server.pem'), 'ca_file': str(self.root / 'ca.pem'),
            'login': self.login, 'equipment_bind': '127.0.0.1', 'equipment_port': equipment,
            'management_port': management, 'server_python': sys.executable, 'firmware_version': 'example',
            'server_environment': {'OPENAVC_PORT': str(server), 'OPENAVC_BIND': '127.0.0.1',
                'OPENAVC_CLOUD_ENABLED': 'false', 'OPENAVC_MDNS_ADVERTISE': 'false', 'OPENAVC_PORT80_REDIRECT': 'false'}}
        (self.root / 'config.json').write_text(json.dumps(self.config))
        self.manager = await self.launch('manager', [sys.executable, '-m', 'openavc_oem', '--config', str(self.root / 'config.json')])
        self.assertEqual(await self.line(self.manager), 'READY')
        self.management = f'http://127.0.0.1:{management}'
        self.equipment = f'https://127.0.0.1:{equipment}'
        self.server = f'http://127.0.0.1:{server}'

    async def line(self, process):
        return (await asyncio.wait_for(process.stdout.readline(), 10)).decode().strip()

    async def test_ipv6_equipment_listener_does_not_start_ipv4_discovery(self):
        self.manager.terminate()
        await asyncio.wait_for(self.manager.wait(), 15)
        self.config['equipment_bind'] = '::'
        (self.root / 'config.json').write_text(json.dumps(self.config))
        self.manager = await self.launch('manager-ipv6', [sys.executable, '-m', 'openavc_oem',
                                        '--config', str(self.root / 'config.json')])
        self.assertEqual(await self.line(self.manager), 'READY')
        endpoint = f'https://[::1]:{self.config["equipment_port"]}'
        auth = BasicAuth(self.login['username'], self.login['password'])
        headers = {'X-OpenAVC-Client': secrets.token_hex(16)}
        async with self.http.get(endpoint + '/v1/description', ssl=self.tls, auth=auth) as response:
            self.assertEqual(response.status, 200)
            description = await response.json()
        async with self.http.post(endpoint + '/v1/command', ssl=self.tls, auth=auth, headers=headers,
            json={'session': description['session'], 'id': 1, 'command': 'increment', 'params': {}}) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())['outcome'], 'completed')
        self.product.stdin.write(b'snapshot\n')
        await self.product.stdin.drain()
        physical = await self.line(self.product)
        self.assertTrue(physical.startswith('STATE 1 1 0 2 1 '), physical)
        settings = await self.get_settings()
        self.assertTrue(settings['discovery'])
        self.assertFalse(settings['equipment']['discovery_active'])
        self.assertIn('IPv4', settings['discovery_error'])
        (ROOT / 'build/ipv6-equipment.json').write_text(json.dumps({
            'status': 'passed', 'bind': '::', 'verified_tls': True,
            'identity': description['identity'], 'physical': physical,
            'discovery_active': settings['equipment']['discovery_active'],
            'discovery_error': settings['discovery_error']}, indent=2) + '\n')

    async def launch(self, name, command, **kwargs):
        log = (self.root / (name + '.log')).open('w')
        self.logs.append(log)
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=log, **kwargs)
        self.processes.append(process)
        return process

    async def shutdown(self):
        if hasattr(self, 'product') and self.product.returncode is None:
            self.product.stdin.write(b'finish\n')
            await self.product.stdin.drain()
        for process in reversed(self.processes):
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 15)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        for log in self.logs:
            log.close()
        saved = ROOT / 'build/host-system-logs'
        saved.mkdir(exist_ok=True)
        for log in sorted(self.root.rglob('*.log')):
            shutil.copy2(log, saved / log.name)

    async def get_settings(self):
        async with self.http.get(self.management + '/oem/settings', headers={
                'Authorization': 'Bearer ' + self.management_token}) as response:
            self.assertEqual(response.status, 200, await response.text())
            return await response.json()

    async def setting(self, field, enabled, expected=200):
        current = await self.get_settings()
        async with self.http.post(self.management + '/oem/settings', headers={
                'Authorization': 'Bearer ' + self.management_token}, json={
                    'revision': current['revision'], 'field': field, 'enabled': enabled}) as response:
            self.assertEqual(response.status, expected, await response.text())
            return await response.json()

    async def hardware(self, command='snapshot'):
        self.product.stdin.write((command + '\n').encode())
        await self.product.stdin.drain()
        row = (await self.line(self.product)).split()
        self.assertEqual(row[0], 'STATE', row)
        return {'writes': int(row[1]), 'callbacks': int(row[2]), 'wrong_threads': int(row[3]),
                'counter': int(row[5]), 'pending': int(row[13])}

    async def hardware_until(self, predicate):
        async with asyncio.timeout(5):
            while True:
                state = await self.hardware()
                if predicate(state):
                    return state
                await asyncio.sleep(.02)

    async def local_connected(self):
        async with asyncio.timeout(10):
            while True:
                devices = (await self.api('GET', '/api/devices'))['devices']
                if any(device['id'] == 'local' and device['connected'] for device in devices):
                    return
                await asyncio.sleep(.1)

    async def test_role_changes_wait_for_hardware_after_a_lost_response(self):
        evidence = {'status': 'running'}
        destination = ROOT / 'build/role-interruption.json'
        try:
            client = Client(self.equipment, self.login, self.root / 'ca.pem')
            description = await self.equipment_response(200)
            # The simulated product owns completion. A role transition cannot
            # release this deliberately held operation or erase its context.
            await self.hardware('delay 60000')
            await asyncio.to_thread(client.interrupt_command, {
                'session': description['session'], 'id': 1, 'command': 'increment', 'params': {}}, 'before_response')
            pending = await self.hardware_until(lambda state: state['pending'])
            self.assertEqual((pending['callbacks'], pending['writes']), (1, 0))
            refused = (await self.setting('server', True, expected=503))['state']
            self.assertTrue(refused['server'])
            self.assertFalse(refused['server_running'])
            self.assertTrue(refused['equipment']['reserved_locally'])
            await self.equipment_response(403)
            blocked = await self.hardware()
            self.assertEqual(blocked, pending)
            evidence['start_while_remote_command_pending'] = {'hardware': blocked, 'settings': refused}
            await self.hardware('finish')
            complete = await self.hardware_until(lambda state: not state['pending'])
            self.assertEqual((complete['callbacks'], complete['writes'], complete['counter']), (1, 1, 1))
            self.assertTrue((await self.setting('server', True))['server_running'])
            await self.api('POST', '/api/auth/setup', self.login)
            await self.local_connected()

            await self.hardware('delay 60000')
            await self.api('POST', '/api/devices/local/command', {'command': 'increment', 'params': {}}, expected=409)
            pending = await self.hardware()
            self.assertNotEqual(pending['pending'], 0)
            self.assertEqual((pending['callbacks'], pending['writes'], pending['counter']), (2, 1, 1))
            refused = (await self.setting('server', False, expected=503))['state']
            self.assertFalse(refused['server'])
            self.assertFalse(refused['server_running'])
            self.assertTrue(refused['equipment']['reserved_locally'])
            await self.equipment_response(403)
            self.assertEqual(await self.hardware(), pending)
            evidence['stop_while_local_command_pending'] = {'hardware': pending, 'settings': refused}
            await self.hardware('finish')
            complete = await self.hardware_until(lambda state: not state['pending'])
            self.assertEqual((complete['callbacks'], complete['writes'], complete['counter']), (2, 2, 2))
            released = await self.setting('server', False)
            self.assertFalse(released['equipment']['reserved_locally'])
            description = await self.equipment_response(200)
            status, reply = await asyncio.to_thread(client.request, '/v1/command', {
                'session': description['session'], 'id': 2, 'command': 'increment', 'params': {}})
            self.assertEqual((status, reply['outcome']), (200, 'completed'))
            complete = await self.hardware()
            self.assertEqual((complete['callbacks'], complete['writes'], complete['counter']), (3, 3, 3))
            self.assertEqual(complete['wrong_threads'], 0)
            evidence.update(status='passed', hardware_after=complete)
        finally:
            destination.write_text(json.dumps(evidence, indent=2) + '\n')

    async def equipment_response(self, expected):
        async with self.http.get(self.equipment + '/v1/description',
                auth=BasicAuth(self.login['username'], self.login['password']),
                ssl=self.tls) as response:
            self.assertEqual(response.status, expected, await response.text())
            return await response.json()

    async def api(self, method, path, body=None, expected=200):
        async with self.http.request(method, self.server + path, json=body,
                auth=BasicAuth(self.login['username'], self.login['password'])) as response:
            self.assertEqual(response.status, expected, await response.text())
            return await response.json()

    async def test_native_endpoint_refuses_blank_unit_identity(self):
        for number, identity in enumerate(('', ' \t\n')):
            with self.subTest(identity=identity):
                environment = {**os.environ, 'OPENAVC_DEVICE_ID': identity,
                               'OPENAVC_TOKEN': secrets.token_hex(32)}
                environment.pop('OPENAVC_PRODUCT_SOCKET', None)
                process = await self.launch('blank-identity-' + str(number),
                    [str(ROOT / 'build/example-c'), self.config['model'], str(self.root / 'server.pem')],
                    env=environment)
                self.assertEqual(await self.line(process), '')
                self.assertEqual(await asyncio.wait_for(process.wait(), 5), 2)
                self.assertIn('identity', (self.root / ('blank-identity-' + str(number) + '.log')).read_text())

    async def test_remote_request_boundaries(self):
        evidence = {'status': 'running', 'refusals': []}
        destination = ROOT / 'build/request-boundaries-result.json'
        try:
            description = await self.equipment_response(200)
            headers = {'Authorization': BasicAuth(self.login['username'], self.login['password']).encode(),
                       'X-OpenAVC-Client': secrets.token_hex(16), 'Content-Type': 'application/json'}
            body = json.dumps({'session': description['session'], 'id': 1,
                               'command': 'increment', 'params': {}}).encode()
            cases = [
                ('malformed_json', b'{', headers, 422),
                ('invalid_utf8', b'\xff', headers, 422),
                ('array_body', b'[]', headers, 422),
                ('duplicate_field', b'{"id":1,' + body[1:], headers, 422),
                ('nested_body', b'[' * 32 + b'0' + b']' * 32, headers, 422),
                ('oversized_body', body + b' ' * (MAX_MESSAGE_BYTES + 1 - len(body)), headers, 413),
                ('invalid_client', body, {**headers, 'X-OpenAVC-Client': 'g' * 32}, 422),
                ('missing_client', body, {k: v for k, v in headers.items() if k != 'X-OpenAVC-Client'}, 422),
                ('duplicate_authorization', body,
                 [*headers.items(), ('Authorization', headers['Authorization'])], 401),
            ]
            before = await self.hardware()
            for name, raw, supplied, status in cases:
                async with self.http.post(self.equipment + '/v1/command', headers=supplied,
                                         data=raw, ssl=self.tls) as response:
                    text = await response.text()
                    self.assertEqual(response.status, status, (name, text))
                after = await self.hardware()
                self.assertEqual(after, before, name)
                evidence['refusals'].append({'case': name, 'http_status': status,
                                            'body_bytes': len(raw), 'hardware': after})
            # The documented maximum is inclusive. A larger request was refused
            # above; a valid request exactly at the boundary must still work.
            raw = body + b' ' * (MAX_MESSAGE_BYTES - len(body))
            async with self.http.post(self.equipment + '/v1/command', headers=headers,
                                     data=raw, ssl=self.tls) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())['outcome'], 'completed')
            after = await self.hardware()
            self.assertEqual((after['writes'], after['callbacks'], after['counter']), (1, 1, 1))
            self.assertEqual(after['wrong_threads'], 0)
            evidence.update(status='passed', maximum_body_bytes=len(raw), hardware_after=after)
        except BaseException as error:
            evidence.update(status='failed', error=str(error))
            raise
        finally:
            destination.write_text(json.dumps(evidence, indent=2) + '\n')

    async def test_independent_controllers_and_shared_equipment(self):
        evidence = {'status': 'running'}
        destination = ROOT / 'build/controller-authority-result.json'
        try:
            other_root = self.root / 'other-product'
            other_root.mkdir(mode=0o700)
            other = await self.launch('other-hardware', [str(ROOT / 'build/example-c'), self.config['model'], '-'],
                env={**os.environ, 'OPENAVC_PRODUCT_SOCKET': str(other_root / 'product.sock')})
            self.assertEqual(await self.line(other), 'READY 0')
            # Deliberately copy the unit identity to a second independent
            # hardware process. Both ordinary device views must expose the
            # duplicate; a shared identity is not a global controller election.
            other_config = {**self.config, 'identity': self.config['identity'],
                            'data_dir': str(other_root / 'data'), 'bridge_path': str(other_root / 'product.sock'),
                            'equipment_port': free_port(), 'management_port': free_port()}
            other_config.pop('server_python')
            config_file = other_root / 'config.json'
            config_file.write_text(json.dumps(other_config))
            manager = await self.launch('other-manager', [sys.executable, '-m', 'openavc_oem', '--config', str(config_file)])
            self.assertEqual(await self.line(manager), 'READY')

            async def other_hardware():
                other.stdin.write(b'snapshot\n')
                await other.stdin.drain()
                values = (await self.line(other)).split()
                self.assertEqual(values[0], 'STATE')
                return {'writes': int(values[1]), 'callbacks': int(values[2]),
                        'wrong_threads': int(values[3]), 'counter': int(values[5])}

            def device(identifier, port):
                return {'id': identifier, 'name': identifier, 'driver': 'openavc_equipment',
                        'config': {'host': '127.0.0.1', 'port': port, **self.login}}

            controllers = []
            for number, product_port in enumerate((self.config['equipment_port'], other_config['equipment_port'])):
                data = self.root / ('controller-' + str(number))
                data.mkdir()
                project = {'openavc_version': '0.14.0', 'project': {'id': 'independent-' + str(number),
                           'name': 'Independent system'}, 'devices': [device('own', product_port)]}
                (data / 'project.avc').write_text(json.dumps(project))
                port = free_port()
                while port == int(self.config['server_environment']['OPENAVC_PORT']):
                    port = free_port()
                await self.launch('controller-' + str(number), [sys.executable, '-m', 'openavc.main'], env={
                    **os.environ, **self.config['server_environment'], 'OPENAVC_PORT': str(port),
                    'OPENAVC_DATA_DIR': str(data), 'OPENAVC_PROJECT': str(data / 'project.avc'),
                    'OPENAVC_ALLOW_ANONYMOUS': 'false', 'SSL_CERT_FILE': str(self.root / 'ca.pem')})
                controllers.append(f'http://127.0.0.1:{port}')

            async def request(number, path, body=None, method='GET', expected=200, headers=None):
                async with self.http.request(method, controllers[number] + path, json=body,
                        headers=headers, auth=BasicAuth(self.login['username'], self.login['password'])) as response:
                    self.assertEqual(response.status, expected, await response.text())
                    return await response.json(), response.headers

            async def connected(number, identifier, desired=True):
                async with asyncio.timeout(30):
                    while True:
                        body, _ = await request(number, '/api/devices/' + identifier)
                        if body['connected'] == desired:
                            return body
                        await asyncio.sleep(.1)

            reported = []
            for number in (0, 1):
                async with asyncio.timeout(30):
                    while True:
                        try:
                            async with self.http.get(controllers[number] + '/api/health') as response:
                                if response.status == 200:
                                    break
                                self.assertEqual(response.status, 503, await response.text())
                        except OSError:
                            pass
                        await asyncio.sleep(.1)
                await request(number, '/api/auth/setup', self.login, 'POST')
                state = await connected(number, 'own')
                reported.append(state['state']['_sdk_identity'])
            self.assertEqual(reported, [self.config['identity']] * 2)
            self.assertNotEqual(self.product.pid, other.pid)
            evidence['cloned_identity'] = {'reported_equipment_ids': reported,
                'independent_hardware_processes': [self.product.pid, other.pid],
                'automatic_duplicate_warning': False}
            command = {'command': 'increment', 'params': {}}
            await asyncio.gather(request(0, '/api/devices/own/command', command, 'POST'),
                                 request(1, '/api/devices/own/command', command, 'POST'))
            self.assertEqual((await self.hardware())['counter'], 1)
            self.assertEqual((await other_hardware())['counter'], 1)
            evidence['independent_systems'] = {'first': await self.hardware(), 'second': await other_hardware()}

            project, headers = await request(1, '/api/project')
            project['devices'].append(device('shared', self.config['equipment_port']))
            await request(1, '/api/project', project, 'PUT', headers={'If-Match': headers['ETag']})
            await connected(1, 'shared')
            await asyncio.gather(request(0, '/api/devices/own/command', command, 'POST'),
                                 request(1, '/api/devices/shared/command', command, 'POST'))
            shared = await self.hardware()
            self.assertEqual((shared['writes'], shared['callbacks'], shared['counter']), (3, 3, 3))
            evidence['two_authorized_writers'] = shared

            await self.setting('server', True)
            await self.api('POST', '/api/auth/setup', self.login)
            await self.local_connected()
            await connected(0, 'own', False)
            await connected(1, 'shared', False)
            for number, identifier in ((0, 'own'), (1, 'shared')):
                await request(number, '/api/devices/' + identifier + '/command', command, 'POST', expected=503)
            await self.api('POST', '/api/devices/local/command', command)
            await request(1, '/api/devices/own/command', command, 'POST')
            self.assertEqual((await self.hardware())['counter'], 4)
            self.assertEqual((await other_hardware())['counter'], 2)
            evidence['active_host_protection'] = {'first': await self.hardware(), 'second': await other_hardware()}

            await self.setting('server', False)
            await asyncio.gather(connected(0, 'own'), connected(1, 'shared'))
            recovered = await self.hardware()
            self.assertEqual((recovered['writes'], recovered['callbacks'], recovered['counter']), (4, 4, 4))
            await asyncio.gather(request(0, '/api/devices/own/command', command, 'POST'),
                                 request(1, '/api/devices/shared/command', command, 'POST'))
            final, independent = await self.hardware(), await other_hardware()
            self.assertEqual((final['writes'], final['callbacks'], final['counter']), (6, 6, 6))
            self.assertEqual((independent['writes'], independent['callbacks'], independent['counter']), (2, 2, 2))
            self.assertEqual((final['wrong_threads'], independent['wrong_threads']), (0, 0))
            evidence.update(status='passed', after_recovery=final, other_product=independent)
        except BaseException as error:
            evidence.update(status='failed', error=str(error))
            raise
        finally:
            destination.write_text(json.dumps(evidence, indent=2) + '\n')

    async def test_occupied_server_listener_retains_intent_and_can_be_retried(self):
        occupied = socket.socket()
        occupied.bind(('127.0.0.1', int(self.config['server_environment']['OPENAVC_PORT'])))
        occupied.listen()
        try:
            refused = await self.setting('server', True, expected=503)
            self.assertTrue(refused['state']['server'])
            self.assertFalse(refused['state']['server_running'])
            self.assertTrue(refused['state']['equipment']['reserved_locally'])
            self.assertIn('bind address and port', refused['detail'])
            await self.equipment_response(403)
            # The manufacturer's application still accepts its own local work.
            before = await self.hardware('external -17')
            self.assertEqual(before['writes'], 0)
        finally:
            occupied.close()
        resumed = await self.setting('server', True)
        self.assertTrue(resumed['server_running'])
        await self.api('POST', '/api/auth/setup', self.login)
        await self.local_connected()
        await self.api('POST', '/api/devices/local/command', {'command': 'increment', 'params': {}})
        after = await self.hardware()
        self.assertEqual((after['writes'], after['callbacks'], after['counter']), (1, 1, 1))
        (ROOT / 'build/resource-startup.json').write_text(json.dumps({
            'status': 'passed', 'failed_start': refused, 'hardware_before': before,
            'hardware_after_retry': after}, indent=2) + '\n')

    async def test_result_and_client_capacity_refuse_excess_without_replaying(self):
        auth = BasicAuth(self.login['username'], self.login['password'])
        description = await self.equipment_response(200)
        client = secrets.token_hex(16)

        async def command(identity, number, expected=200):
            async with self.http.post(self.equipment + '/v1/command', ssl=self.tls, auth=auth,
                headers={'X-OpenAVC-Client': identity}, json={'session': description['session'],
                'id': number, 'command': 'increment', 'params': {}}) as response:
                self.assertEqual(response.status, expected, await response.text())
                return await response.json()

        for number in range(1, 133):
            self.assertEqual((await command(client, number))['outcome'], 'completed')
        before_expired = await self.hardware()
        self.assertEqual(before_expired['writes'], 132)
        expired = await command(client, 1, 409)
        self.assertEqual(expired['error'], 'request_expired')
        self.assertEqual(await self.hardware(), before_expired)
        for _ in range(127):
            await command(secrets.token_hex(16), 1)
        before_capacity = await self.hardware()
        capacity = await command(secrets.token_hex(16), 1, 429)
        self.assertEqual(capacity['error'], 'client_capacity')
        self.assertEqual(await self.hardware(), before_capacity)
        async with self.http.get(self.equipment + '/health', ssl=self.tls, auth=auth) as response:
            self.assertEqual(response.status, 200)
            health = await response.json()
        self.assertEqual(health['result_cache'], 128)
        self.assertEqual(health['pending'], 0)
        await command(client, 133)
        after = await self.hardware()
        self.assertEqual((after['writes'], after['callbacks'], after['counter']), (260, 260, 260))
        (ROOT / 'build/resource-capacity.json').write_text(json.dumps({
            'status': 'passed', 'expired': expired, 'capacity': capacity,
            'health_at_capacity': health, 'hardware_after': after}, indent=2) + '\n')

    async def test_remote_and_local_use_the_same_hardware(self):
        description = await self.equipment_response(200)
        self.assertEqual(description['identity'], self.config['identity'])
        status = await self.setting('server', True)
        self.assertTrue(status['server_running'])
        self.assertTrue(status['equipment']['reserved_locally'])
        self.assertFalse(status['equipment']['discovery_active'])
        await self.equipment_response(403)
        await self.api('POST', '/api/auth/setup', self.login)
        for _ in range(100):
            devices = (await self.api('GET', '/api/devices'))['devices']
            local = next((device for device in devices if device['id'] == 'local'), {})
            if local.get('connected'):
                break
            await asyncio.sleep(.05)
        self.assertTrue(local.get('connected'), local)
        await self.api('POST', '/api/devices/local/command', {'command': 'set_gain', 'params': {'channel': 1, 'gain': -12.5}})
        self.product.stdin.write(b'snapshot\n')
        await self.product.stdin.drain()
        physical = (await self.line(self.product)).split()
        self.assertEqual(physical[0], 'STATE')
        self.assertEqual(float(physical[7]), -12.5)
        await self.api('DELETE', '/api/devices/local', expected=409)
        await self.setting('programmer', False)
        await self.api('GET', '/api/devices', expected=403)
        await self.api('GET', '/api/health')
        await self.setting('programmer', True)
        await self.api('GET', '/api/devices')
        status = await self.setting('server', False)
        self.assertFalse(status['server_running'])
        self.assertFalse(status['equipment']['reserved_locally'])
        await self.equipment_response(200)
        # The manufacturer process is independent of the OpenAVC server.
        self.assertIsNone(self.product.returncode)
        await self.setting('equipment', False)
        await self.equipment_response(403)
        await self.setting('equipment', True)
        await self.equipment_response(200)


if __name__ == '__main__':
    unittest.main()
