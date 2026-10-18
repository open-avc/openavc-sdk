"""Observe idle credentials through the installed product middleware composition."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InterfaceCredentials(unittest.TestCase):
    def test_idle_plugin_streams_do_not_renew_sessions_or_repeat_password_hashing(self):
        code = r'''
import asyncio
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import APIRouter
from starlette.responses import StreamingResponse
from openavc import main
from openavc.api import auth, plugin_ext, session_tokens
from openavc.system_config import get_system_config
from openavc_oem.interfaces import install
from openavc_oem.state import defaults

# No lifespan or Engine.start: real credentials, routes and both policy owners.
Path(os.environ['OPENAVC_INTERFACE_STATE']).write_text(json.dumps(defaults('credential-example', initial=True)))
cfg = get_system_config()
cfg.set('auth', 'programmer_username', 'engineer')
cfg.set('auth', 'api_key', '')
auth.store_admin_password('disposable-password')
interfaces = install(main.app, 'credential-example')
main.configure_host_policy(interfaces.host_policy)
clock = [1000.0]
store = session_tokens.SessionTokenStore(ttl=10)
checks = []
check = auth._check_credentials
def checked(user, password):
    checks.append(True)
    return check(user, password)

async def run():
    ready, finished = asyncio.Event(), asyncio.Event()
    router = APIRouter()
    @router.get('/stream')
    async def stream():
        async def chunks():
            try:
                yield b'initial'
                ready.set()
                await asyncio.Future()
            finally:
                finished.set()
        return StreamingResponse(chunks())
    plugin_ext.mount_plugin_router(main.app, 'credential_example', router)
    for kind in ('basic', 'session'):
        ready.clear()
        finished.clear()
        token, _ = store.issue(auth.credential_fingerprint())
        header = (b'Basic ' + base64.b64encode(b'engineer:disposable-password')
                  if kind == 'basic' else ('Bearer ' + token).encode())
        path = '/api/plugins/credential_example/ext/stream'
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                 'method': 'GET', 'path': path, 'raw_path': path.encode(),
                 'root_path': '', 'query_string': b'', 'scheme': 'http',
                 'server': ('test', 80), 'client': ('192.0.2.1', 1234),
                 'http_version': '1.1', 'headers': [(b'authorization', header)]}
        sent = []
        async def receive():
            await asyncio.Future()
        async def send(message):
            sent.append(message)
        task = asyncio.create_task(main.app(scope, receive, send))
        try:
            await asyncio.wait_for(ready.wait(), 2)
            before = len(checks)
            clock[0] += 9
            await asyncio.sleep(.25)
            assert not task.done()
            assert len(checks) == before, 'Idle classification repeated password verification'
            if kind == 'basic':
                auth.store_admin_password('replacement-password')
            else:
                clock[0] += 2
            await asyncio.wait_for(task, 2)
            assert finished.is_set()
            assert sent[0]['status'] == 200
            assert sent[-1] == {'type': 'http.response.body', 'body': b'', 'more_body': False}
            assert not interfaces.connections
            assert not interfaces.host_policy._admissions
            if kind == 'session':
                assert not store.validate(token, auth.credential_fingerprint(), refresh=False)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    plugin_ext.unmount_plugin_router(main.app, 'credential_example')

with patch.object(session_tokens, 'time', SimpleNamespace(time=lambda: clock[0])), \
     patch.object(session_tokens, 'store', store), patch.object(auth, '_check_credentials', checked):
    asyncio.run(run())
print(json.dumps({'core': main.__file__, 'provider': __import__('openavc_oem.interfaces', fromlist=['']).__file__,
                  'idle_password_checks': 0, 'expired_session_refused': True, 'streams_closed': 2}))
'''
        with tempfile.TemporaryDirectory(prefix='sdk-interface-credentials-') as root:
            env = {**os.environ, 'OPENAVC_DATA_DIR': str(Path(root) / 'data'),
                   'OPENAVC_PROJECT': str(Path(root) / 'data/projects/example/project.avc'),
                   'OPENAVC_INTERFACE_STATE': str(Path(root) / 'interfaces.json'),
                   'OPENAVC_HOST_CONTROL_TOKEN': 'a' * 64}
            result = subprocess.run([sys.executable, '-c', code], env=env,
                                    cwd=Path(__file__).resolve().parents[1],
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout)
