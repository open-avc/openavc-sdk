"""Compile generated C callbacks and exercise the real TLS command endpoint.

Run with Python 3.12+, CMake, a C/C++ toolchain and OpenSSL 3 installed:
    python -m unittest discover -s tests -v
"""

import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


class ModelIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="openavc-model-")
        cls.addClassCleanup(cls.folder.cleanup)
        work = Path(cls.folder.name)
        model = json.loads((ROOT / "product.json").read_text())
        model["state_variables"] = {
            "writes": {"type": "integer", "label": "Completed Writes"},
            "mute_present": {"type": "boolean", "label": "Mute Supplied"},
            "mute": {"type": "boolean", "label": "Mute"}, "label": {"type": "string", "label": "Label"},
            "revision": {"type": "integer", "label": "Firmware Revision"},
            "quality": {"type": "string", "label": "Signal Quality"},
            "reading": {"type": "float", "label": "Reading"},
            "selection": {"type": "enum", "values": ["USB-C", 3], "label": "Selection"},
            "data": {"type": "string", "label": "Product Data"},
        }
        model["child_entity_types"] = {
            "output": {"id_format": {"type": "string"}, "state_variables": {"gain": {"type": "number"}}},
            "input": {"state_variables": {"active": {"type": "boolean"}}},
        }
        model["commands"] = {"configure": {"params": {
            "output": {"type": "child_id", "child_type": "output", "required": True},
            "input": {"type": "child_id", "child_type": "input", "required": True, "default": 2},
            "gain": {"type": "number", "min": -80, "max": 12, "default": -18},
            "label": {"type": "string", "trim": False}, "mute": {"type": "boolean"},
            "mode": {"type": "enum", "values": [{"value": "analog", "label": "Analog Input"}, "USB-C", 3]},
        }}}
        (work / "product.json").write_text(json.dumps(model))
        (work / "CMakeLists.txt").write_text(f'''cmake_minimum_required(VERSION 3.16)
project(model_test LANGUAGES C CXX)
set(OPENAVC_PRODUCT "{work / 'product.json'}" CACHE FILEPATH "")
set(OPENAVC_BUILD_EXAMPLES OFF CACHE BOOL "")
add_subdirectory("{ROOT}" sdk)
add_executable(model-test "{ROOT / 'tests/hardware_model.c'}")
target_compile_options(model-test PRIVATE -Wall -Wextra -Werror)
target_link_libraries(model-test PRIVATE adapter)
''')

        def run(args):
            result = subprocess.run(args, capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)

        run(["cmake", "-S", str(work), "-B", str(work / "build"), f"-DPython3_EXECUTABLE={sys.executable}"])
        run(["cmake", "--build", str(work / "build"), "--target", "model-test", "-j", "4"])
        run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-keyout", str(work / "key.pem"), "-out", str(work / "cert.pem"),
             "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1"])
        (work / "server.pem").write_bytes((work / "cert.pem").read_bytes() + (work / "key.pem").read_bytes())
        cls.token = secrets.token_hex(32)
        cls.context = ssl.create_default_context(cafile=str(work / "cert.pem"))
        cls.process = subprocess.Popen([str(work / "build/model-test"), str(work / "build/sdk/generated/model.json"),
                                        str(work / "server.pem")], text=True, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env={**os.environ, "OPENAVC_TEST_TOKEN": cls.token})
        cls.addClassCleanup(cls.stop)
        line = cls.process.stdout.readline().strip()
        if not line.startswith("READY "):
            raise RuntimeError("Equipment did not start: " + line)
        cls.url = "https://127.0.0.1:" + line.split()[1]
        cls.next_id = 0
        cls.completed = 0
        cls.client_id = secrets.token_hex(16)
        cls.session = cls.ready()["session"]

    @classmethod
    def stop(cls):
        if cls.process.poll() is None:
            cls.process.stdin.write("\n")
            cls.process.stdin.flush()
        try:
            stdout, stderr = cls.process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            stdout, stderr = cls.process.communicate()
            raise AssertionError("Equipment did not stop: " + stderr)
        if cls.process.returncode:
            raise AssertionError(f"Equipment exited {cls.process.returncode}: {stdout}{stderr}")

    @classmethod
    def request(cls, path, data=None):
        request = urllib.request.Request(cls.url + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={"Authorization": "Bearer " + cls.token, "X-OpenAVC-Client": cls.client_id,
                     "Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(request, context=cls.context, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    @classmethod
    def ready(cls, writes=None):
        writes = cls.completed if writes is None else writes
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, snapshot = cls.request("/v1/snapshot")
            if status == 200 and snapshot["state"]["writes"] >= writes:
                return snapshot
            time.sleep(.02)
        raise AssertionError(f"Readback unavailable: {status} {snapshot}")

    def command(self, params):
        type(self).next_id += 1
        response = self.request("/v1/command", {"session": self.session, "id": self.next_id,
                                               "command": "configure", "params": params})
        if response[0] == 200 and response[1].get("outcome") == "completed":
            type(self).completed += 1
        return response

    def test_optional_default_and_named_channels(self):
        before = self.ready()["state"]["writes"]
        status, outcome = self.command({"output": "Main-L", "input": 2})
        self.assertEqual((status, outcome["outcome"]), (200, "completed"))
        snapshot = self.ready(before + 1)
        self.assertFalse(snapshot["state"]["mute_present"])
        self.assertEqual(snapshot["state"]["revision"], 42)
        self.assertEqual(snapshot["state"]["quality"], "manufacturer-value")
        self.assertEqual(snapshot["quality"], "current")
        self.assertEqual(snapshot["state"]["reading"], 1.25)
        self.assertEqual(snapshot["state"]["selection"], 3)
        self.assertIs(type(snapshot["state"]["selection"]), int)
        self.assertEqual(snapshot["children"][:2], [
            {"type": "output", "id": "Main-L", "state": {"gain": -18}},
            {"type": "input", "id": 2, "state": {"active": True}},
        ])
        self.assertEqual(len(snapshot["children"]), 130)
        self.assertEqual(snapshot["state"]["data"], "x" * 40000)

    def test_present_false_and_verbatim_string(self):
        before = self.ready()["state"]["writes"]
        status, outcome = self.command({"output": "Main-L", "input": 2, "gain": -9.5,
                                        "label": "  Room A  ", "mute": False, "mode": "analog"})
        self.assertEqual((status, outcome["outcome"]), (200, "completed"))
        state = self.ready(before + 1)["state"]
        self.assertTrue(state["mute_present"])
        self.assertFalse(state["mute"])
        self.assertEqual(state["label"], "  Room A  ")

    def test_enum_values_keep_their_declared_type_and_spelling(self):
        for value in ["USB-C", 3]:
            with self.subTest(value=value):
                status, outcome = self.command({"output": "Main-L", "input": 2, "mode": value})
                self.assertEqual((status, outcome["outcome"]), (200, "completed"))

    def test_invalid_requests_do_not_call_hardware(self):
        before = self.ready()["state"]["writes"]
        for params in [
            {"output": "Main-L"},
            {"output": "Main-L", "input": None},
            {"output": "Main-L", "input": 2, "gain": 99},
            {"output": "Main-L", "input": 2, "mute": "false"},
            {"output": "Main-L", "input": 2, "extra": 1},
        ]:
            with self.subTest(params=params):
                self.assertEqual(self.command(params)[0], 422)
        self.assertEqual(self.ready()["state"]["writes"], before)


if __name__ == "__main__":
    unittest.main()
