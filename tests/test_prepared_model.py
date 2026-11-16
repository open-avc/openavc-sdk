"""A model the SDK's build prepares connects to OpenAVC with nothing to name.

The build adds an HTTP transport with its settings and each command's request
around the product's own fields. OpenAVC reads a served model through the
contract's equipment surface and names any other key on the device page, so
everything the build serves must be inside that surface.

Run with Python 3.12+ and the OpenAVC server installed:
    python -m unittest tests.test_prepared_model -v
"""

import asyncio
from datetime import datetime, timedelta, timezone
import ipaddress
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.equipment import EquipmentDriver
from openavc.drivers.equipment_protocol import PROTOCOL
from openavc.drivers.registry import _DRIVER_REGISTRY


ROOT = Path(__file__).resolve().parents[1]


def prepare(product, out):
    """The build's own step, as CMake runs it."""
    return subprocess.run(
        [sys.executable, str(ROOT / "sdk" / "tools" / "prepare_product.py"),
         "--product", str(product), "--core", str(ROOT / "sdk" / "contract"), "--out", str(out)],
        capture_output=True, text=True)


def certificate(folder):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Example product")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    ca = folder / "ca.pem"
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem = folder / "server.pem"
    pem.write_bytes(ca.read_bytes() + key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(pem)
    return ca, tls


class PreparedModel(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_a_key_outside_the_product_surface_is_refused_by_name(self):
        product = json.loads((ROOT / "product.json").read_text())
        for change, refused in [
            (lambda p: p.update(capabilities=[]), "capabilities: not a product field."),
            (lambda p: next(iter(p["commands"].values())).update(description="x"),
             ".description: not a product field."),
            (lambda p: p.update(polling={"interval": 5}), "polling: the SDK supplies connection behavior."),
        ]:
            changed = json.loads(json.dumps(product))
            change(changed)
            path = self.folder / "product.json"
            path.write_text(json.dumps(changed))
            with self.subTest(refused=refused):
                result = prepare(path, self.folder / "model.json")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(refused, result.stderr)

    async def test_the_prepared_starter_connects_with_nothing_named(self):
        result = prepare(ROOT / "product.json", self.folder / "model.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        model = json.loads((self.folder / "model.json").read_text())
        ca, tls = certificate(self.folder)

        async def description(request):
            return web.json_response({"protocol": PROTOCOL, "identity": "unit-1",
                                      "session": "first", "capabilities": model})

        async def snapshot(request):
            return web.json_response({"identity": "unit-1", "session": "first",
                                      "state": {}, "children": []})

        app = web.Application()
        app.router.add_get("/v1/description", description)
        app.router.add_get("/v1/snapshot", snapshot)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        self.enterContext(mock.patch.dict(_DRIVER_REGISTRY, {"openavc_equipment": EquipmentDriver}))
        self.enterContext(mock.patch("openavc.drivers.equipment.get_data_dir", lambda: self.folder))
        state, events = StateStore(), EventBus()
        state.set_event_bus(events)
        manager = DeviceManager(state, events)
        self.addAsyncCleanup(manager.disconnect_all)
        await manager.add_device({"id": "amp", "driver": "openavc_equipment", "name": "Amp", "config": {
            "host": "127.0.0.1", "port": port, "username": "engineer", "password": "fixture",
            "ca_file": str(ca)}})
        async with asyncio.timeout(10):
            while not state.get("device.amp.connected"):
                await asyncio.sleep(.05)
        self.assertNotIn("model_notices", manager.get_device_info("amp"))


if __name__ == "__main__":
    unittest.main()
