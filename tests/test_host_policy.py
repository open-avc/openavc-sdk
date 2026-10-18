"""Product settings and management acknowledgements use canonical core policy."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from fastapi import FastAPI, HTTPException, Request

from openavc.api.host_policy import HostPolicyMiddleware, validate_policies
from openavc.core.host_policy import HostCategory
from openavc_oem.host_policy import FileHostPolicy
from openavc_oem.manager import ProductManager
from openavc_oem.state import defaults


class HostPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sdk-host-policy-')
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'interfaces.json'
        self.token = 'a' * 64
        self.state = defaults('example-product', initial=True)
        self.write()
        self.policy = FileHostPolicy(self.path, 'example-product', self.token)

    def write(self):
        self.path.write_text(json.dumps(self.state))

    def scope(self, *, peer='127.0.0.1', headers=None):
        return {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                'method': 'POST', 'path': '/api/host/interfaces', 'raw_path': b'/api/host/interfaces',
                'root_path': '', 'query_string': b'', 'scheme': 'http', 'http_version': '1.1',
                'server': ('127.0.0.1', 18000), 'client': (peer, 23456),
                'headers': headers if headers is not None else [
                    (b'authorization', ('Bearer ' + self.token).encode())]}

    async def test_invalid_or_foreign_settings_deny_optional_interfaces_only(self):
        self.assertTrue(self.policy.snapshot().programmer_enabled)
        for contents in ['{', json.dumps({**self.state, 'identity': 'another-unit'}),
                         json.dumps({**self.state, 'panel': 1}),
                         '{"revision":1,"revision":2}']:
            self.path.write_text(contents)
            snapshot = self.policy.snapshot()
            self.assertIsNone(snapshot.revision)
            self.assertFalse(snapshot.programmer_enabled)
            self.assertFalse(snapshot.panel_enabled)
            lease = self.policy.admit(HostCategory.RUNTIME)
            lease.close()
        self.path.unlink()
        self.assertIsNone(self.policy.snapshot().revision)

    async def test_management_token_requires_actual_local_origin_and_one_header(self):
        await self.policy.authenticate_host(Request(self.scope()))
        valid = (b'authorization', ('Bearer ' + self.token).encode())
        refused = [self.scope(peer='192.0.2.5'), self.scope(headers=[]),
                   self.scope(headers=[valid, valid]),
                   self.scope(headers=[valid, (b'x-forwarded-for', b'127.0.0.1')]),
                   self.scope(headers=[valid, (b'x-openavc-client', b'example')]),
                   self.scope(headers=[(b'authorization', b'Bearer wrong')])]
        for scope in refused:
            with self.assertRaises(HTTPException) as result:
                await self.policy.authenticate_host(Request(scope))
            self.assertEqual(result.exception.status_code, 403)

    async def test_disconnected_commit_stays_pending_until_its_owner_finishes(self):
        admission = self.policy.admit(HostCategory.PROGRAMMER)
        commit = admission.begin_commit()
        admission.close()
        self.state.update(revision=1, programmer=False)
        self.write()
        response = await self.policy.acknowledge(timeout=0)
        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.body)
        self.assertIs(payload['observed'], False)
        self.assertEqual(payload['pending_connections'], 0)
        self.assertEqual(payload['pending_commits'], 1)
        self.assertFalse(commit.finished)
        commit.finish('committed')
        response = await self.policy.acknowledge(timeout=0)
        self.assertEqual(response.status_code, 200)
        self.assertIs(json.loads(response.body)['observed'], True)

    async def test_existing_owner_must_finish_before_observed_acknowledgement(self):
        done = asyncio.Event()
        response = await self.policy.acknowledge(done.wait, timeout=0.01)
        self.assertEqual(response.status_code, 503)
        self.assertIs(json.loads(response.body)['pending_existing_operations'], True)
        self.assertFalse(done.is_set())
        done.set()
        response = await self.policy.acknowledge(done.wait)
        self.assertEqual(response.status_code, 200)
        self.assertIs(json.loads(response.body)['observed'], True)

    async def test_real_endpoint_refuses_management_before_consuming_a_body(self):
        app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
        self.policy.install_endpoint(app)
        app.add_middleware(HostPolicyMiddleware)
        validate_policies(app)
        sent = []

        async def receive():
            raise AssertionError('Management endpoint consumed a request body')

        async def send(message):
            sent.append(message)

        await app(self.scope(headers=[(b'transfer-encoding', b'chunked')]), receive, send)
        self.assertEqual(sent[0]['status'], 403)
        sent.clear()
        self.state.update(revision=2, programmer=False, panel=False)
        self.write()
        await app(self.scope(), receive, send)
        self.assertEqual(sent[0]['status'], 200)
        payload = json.loads(sent[-1]['body'])
        self.assertEqual(payload['revision'], 2)
        self.assertIs(payload['programmer'], False)
        self.assertIs(payload['panel'], False)
        self.assertIs(payload['observed'], True)

    async def test_manager_does_not_report_saved_settings_as_observed_without_confirmation(self):
        expected = {'revision': 4, 'programmer': False, 'panel': True}

        class Response:
            payload = {}
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def raise_for_status(self):
                pass
            async def json(self):
                return self.payload

        response = Response()
        manager = SimpleNamespace(
            state=expected, interfaces_observed=expected,
            http=SimpleNamespace(post=lambda *a, **kw: response),
            core_url='http://127.0.0.1:1', core_tls=lambda: False,
            core_control_token=self.token,
        )
        for observed in (None, False, 'true', 1):
            response.payload = {**expected, 'observed': observed}
            with self.assertRaises(RuntimeError):
                await ProductManager.confirm_interfaces(manager)
            self.assertIsNone(manager.interfaces_observed)
        response.payload = {**expected, 'observed': True, 'revision': 3}
        with self.assertRaises(RuntimeError):
            await ProductManager.confirm_interfaces(manager)
        self.assertIsNone(manager.interfaces_observed)
        response.payload = {**expected, 'observed': True}
        await ProductManager.confirm_interfaces(manager)
        self.assertEqual(manager.interfaces_observed, expected)
