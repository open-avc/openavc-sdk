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

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


@unittest.skipUnless(sys.platform == 'linux' and (ROOT / 'build/example-service').exists(),
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
                        '-subj', '/CN=Example equipment', '-addext', 'subjectAltName=IP:127.0.0.1'],
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
            'device_service': str(ROOT / 'build/example-service'), 'bridge_path': str(self.root / 'product.sock'),
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

    async def launch(self, name, command, **kwargs):
        log = (self.root / (name + '.log')).open('w')
        self.logs.append(log)
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=log, **kwargs)
        self.processes.append(process)
        return process

    async def shutdown(self):
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

    async def setting(self, field, enabled):
        current = await self.get_settings()
        async with self.http.post(self.management + '/oem/settings', headers={
                'Authorization': 'Bearer ' + self.management_token}, json={
                    'revision': current['revision'], 'field': field, 'enabled': enabled}) as response:
            self.assertEqual(response.status, 200, await response.text())
            return await response.json()

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
