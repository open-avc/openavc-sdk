"""Canonical routes and the product provider, isolated from engine/network startup."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InterfaceRoutes(unittest.TestCase):
    def test_product_settings_preserve_panel_preview_and_lifecycle_boundaries(self):
        code = r'''
import asyncio
import faulthandler
import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch
faulthandler.dump_traceback_later(10)

def audit(event, args):
    if event in {"socket.connect", "socket.bind", "socket.sendto", "subprocess.Popen", "os.system"}:
        raise RuntimeError("External I/O is forbidden in this route fixture: " + event)
sys.addaudithook(audit)

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from openavc import main
from openavc.api.host_policy import validate_policies
from openavc.core.host_policy import HostPolicy
from openavc.system_config import get_system_config
from openavc_oem.interfaces import Interfaces, install
from openavc_oem.state import defaults

state_path = Path(os.environ['OPENAVC_INTERFACE_STATE'])
state = defaults('route-example', initial=True)
def save(**changes):
    state.update(changes)
    state['revision'] += 1
    state_path.write_text(json.dumps(state))
save()
cfg = get_system_config()
cfg.set('auth', 'programmer_username', 'engineer')
cfg.set('auth', 'programmer_password', 'disposable-route-password')
cfg.set('auth', 'api_key', '')
interfaces = install(main.app, 'route-example')
main.configure_host_policy(interfaces.host_policy)
validate_policies(main.app)

# No Engine/lifespan: real routing, auth and response middleware are exercised.
client = TestClient(main.app, client=('127.0.0.1', 23845))
try:
    print('checking panel link and preview', flush=True)
    assert client.get('/panel').status_code == 200
    with client.websocket_connect('/ws?client=preview') as ws:
        try:
            ws.receive_json()
        except WebSocketDisconnect as error:
            assert error.code == 4014
        else:
            raise AssertionError('Unauthenticated Preview was admitted')

    save(programmer=False)
    print('checking programmer disabled', flush=True)
    assert client.get('/panel', follow_redirects=False).status_code == 307
    assert client.get('/panel').status_code == 200
    assert client.get('/panel?edit=1').status_code == 403
    assert client.get('/api/startup-status').status_code == 200
    denied = client.get('/programmer/', headers={'accept': 'text/html'})
    assert denied.status_code == 403
    assert denied.headers['content-type'].startswith('text/html')
    assert denied.headers['cache-control'] == 'no-store'

    with client.websocket_connect('/ws?client=panel') as ws:
        print('checking panel snapshot', flush=True)
        assert ws.receive_json()['type'] == 'state.snapshot'
        save(panel=False)
        print('checking panel close', flush=True)
        try:
            ws.receive_json()
        except WebSocketDisconnect as error:
            assert error.code == 4013
        else:
            raise AssertionError('Disabled panel connection stayed open')
    saved = {key: state[key] for key in ('revision', 'programmer', 'panel')}
    print('checking observed settings', flush=True)
    response = client.post('/api/host/interfaces', headers={'authorization': 'Bearer ' + 'a' * 64})
    assert response.status_code == 200, response.text
    assert response.json()['observed'] is True
    assert {key: response.json()[key] for key in saved} == saved
    assert client.post('/api/host/interfaces', headers={
        'authorization': 'Bearer ' + 'a' * 64, 'x-forwarded-for': '127.0.0.1',
    }).status_code == 403
    assert client.get('/panel/panel.js').json()['code'] == 'interface_disabled'

    state_path.write_text('{')
    assert client.get('/panel').status_code == 403
    assert client.get('/api/startup-status').status_code == 200
finally:
    client.close()

# Force the old/new close race: the first close is blocked in transport send
# when product revocation cancels it. A completed replacement close is required.
async def close_race():
    save(programmer=True, panel=True)
    closing = asyncio.Event()
    sent, attempts = [], []
    async def receive():
        return {'type': 'websocket.connect'}
    async def send(message):
        if message['type'] == 'websocket.close':
            attempts.append(message)
            if len(attempts) == 1:
                closing.set()
                await asyncio.Event().wait()
        sent.append(message)
    async def endpoint(scope, receive, send):
        await receive()
        await send({'type': 'websocket.accept', 'subprotocol': None})
        await send({'type': 'websocket.close', 'code': 4013})
    scope = {'type': 'websocket', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
             'path': '/ws', 'raw_path': b'/ws', 'query_string': b'client=panel',
             'root_path': '', 'scheme': 'ws', 'headers': [],
             'server': ('test', 80), 'client': ('127.0.0.1', 23845)}
    task = asyncio.create_task(Interfaces(endpoint, interfaces)(scope, receive, send))
    try:
        await asyncio.wait_for(closing.wait(), 1)
        save(panel=False)
        await asyncio.wait_for(task, 1)
        assert len(attempts) == 2
        assert len([m for m in sent if m['type'] == 'websocket.close']) == 1
        assert sent[-1]['code'] == 4013
        assert not interfaces.connections
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
asyncio.run(close_race())

# Exercise the actual lifespan's configuration freeze, replacing only startup/
# cleanup owners that would open devices or services. This is not Engine proof.
from openavc.listen_sockets import push_listeners
async def lifecycle():
    with patch.object(main, '_initialize_engine', AsyncMock()), \
         patch.object(main.engine, 'stop', AsyncMock()), \
         patch.object(main.audit_manager, 'shutdown', AsyncMock()), \
         patch.object(main.simulator_proxy, 'aclose_client', AsyncMock()), \
         patch.object(push_listeners, 'stop', AsyncMock()):
        async with main.lifespan(main.app):
            try:
                main.configure_host_policy(HostPolicy())
            except RuntimeError:
                pass
            else:
                raise AssertionError('A running product accepted a different provider')
            assert main.engine.host_policy is interfaces.host_policy
asyncio.run(lifecycle())
faulthandler.cancel_dump_traceback_later()
print(json.dumps({'core': main.__file__, 'provider': sys.modules['openavc_oem.host_policy'].__file__,
                  'status': 'passed', 'engine_startup': False}))
'''
        with tempfile.TemporaryDirectory(prefix='sdk-interface-routes-') as root:
            env = {**os.environ, 'OPENAVC_DATA_DIR': str(Path(root) / 'data'),
                   'OPENAVC_PROJECT': str(Path(root) / 'data/projects/example/project.avc'),
                   'OPENAVC_INTERFACE_STATE': str(Path(root) / 'interfaces.json'),
                   'OPENAVC_HOST_CONTROL_TOKEN': 'a' * 64}
            try:
                result = subprocess.run([sys.executable, '-c', code], env=env,
                                        cwd=Path(__file__).resolve().parents[1],
                                        capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired as error:
                self.fail(f'Route fixture timed out:\n{error.stdout!r}\n{error.stderr!r}')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout)
