"""Retain the OpenAVC listener sockets while supervising its process."""

import asyncio
import errno
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
import ssl

from aiohttp import Fingerprint
from yarl import URL


class NativeHosting:
    def __init__(self):
        super().__init__()
        self.core_endpoint = None
        self.native_listeners = []

    async def prepare_endpoint(self, environment):
        # Import the actual server configuration in a fresh process on every
        # start. No copied defaults or persistent second network configuration.
        process = await asyncio.create_subprocess_exec(
            self.config["server_python"], "-m", "openavc_oem.read_endpoint",
            env=environment, cwd=str(self.data_dir), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, error = await asyncio.wait_for(process.communicate(), 10)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            raise RuntimeError("Could not read OpenAVC listener settings: " + error.decode()[-2048:])
        endpoint = json.loads(output)
        self.core_endpoint = endpoint
        return endpoint["bindings"]

    def reserve_listeners(self, bindings):
        listeners = []
        unavailable = None
        try:
            for binding in bindings:
                try:
                    listener = socket.socket(binding["family"], socket.SOCK_STREAM)
                except OSError as exc:
                    if exc.errno not in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT}:
                        raise
                    unavailable = exc
                    continue
                try:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    if listener.family == socket.AF_INET6:
                        listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    listener.bind(tuple(binding["address"]))
                    listener.listen(128)
                except OSError as exc:
                    listener.close()
                    # Match asyncio's ordinary server behavior when one address
                    # family resolves but is unavailable on this host.
                    if exc.errno == errno.EADDRNOTAVAIL:
                        unavailable = exc
                        continue
                    raise
                listeners.append(listener)
            if not listeners:
                raise unavailable or OSError(errno.EADDRNOTAVAIL, "No configured listening address is available")
            selected = ipaddress.ip_address(listeners[0].getsockname()[0])
            local = str(selected) if not selected.is_unspecified else ("::1" if selected.version == 6 else "127.0.0.1")
            self.core_url = str(URL.build(scheme=self.core_endpoint["scheme"], host=local, port=self.core_endpoint["port"]))
            return listeners
        except BaseException:
            for listener in listeners:
                listener.close()
            raise

    async def stop_core(self):
        try:
            await super().stop_core()
        finally:
            for listener in self.native_listeners:
                listener.close()
            self.native_listeners = []

    def core_tls(self):
        if self.core_endpoint["scheme"] == "http":
            return None
        # The supervisor reads its own server's leaf from local storage. Pin
        # that certificate for internal probes, including provided certificates
        # whose names do not cover loopback. Never learn trust from a response.
        pem = Path(self.core_endpoint["certificate"]).read_text()
        leaf = pem[:pem.index("-----END CERTIFICATE-----") + len("-----END CERTIFICATE-----")]
        return Fingerprint(hashlib.sha256(ssl.PEM_cert_to_DER_cert(leaf)).digest())

    def status(self):
        return {**super().status(), "server_endpoint": self.core_endpoint}
