"""Advertise the product's equipment endpoint on its selected IPv4 interfaces."""

import asyncio
import contextlib
import hashlib
import ipaddress
import socket

import psutil

from ._dns_wire import build_announcement_records, build_dns_response, _parse_query_questions

SERVICE = '_openavc-device._tcp.local.'
GROUP = ('224.0.0.251', 5353)


class Advertiser:
    def __init__(self, identity, port, addresses):
        self.name = 'openavc-equipment-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.port = port
        self.requested_addresses = addresses
        self.addresses = []
        self.enabled = False
        self.properties = {}
        self.socket = None
        self.task = None
        self.error = None

    @property
    def active(self):
        return (self.enabled and self.socket is not None
                and self.task is not None and not self.task.done())

    def selected_addresses(self):
        if self.requested_addresses is not None:
            return sorted(set(self.requested_addresses))
        return sorted({entry.address for entries in psutil.net_if_addrs().values() for entry in entries
                       if entry.family == socket.AF_INET and not ipaddress.ip_address(entry.address).is_loopback})

    async def start(self):
        await self.refresh_interfaces()
        self.task = asyncio.create_task(self.listen())

    async def refresh_interfaces(self):
        addresses = self.selected_addresses()
        if self.socket is not None and addresses == self.addresses:
            return
        if self.socket is not None:
            with contextlib.suppress(OSError):
                await self.send(0)
            self.socket.close()
            self.socket = None
        self.addresses = addresses
        if not addresses:
            self.error = 'No network interface is available for equipment discovery.'
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, 'SO_REUSEPORT'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(('', GROUP[1]))
            for address in self.addresses:
                ipaddress.IPv4Address(address)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                socket.inet_aton(GROUP[0]) + socket.inet_aton(address))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.setblocking(False)
        except OSError as error:
            sock.close()
            self.error = str(error)
            return
        self.socket = sock
        self.error = None
        if self.enabled:
            await self.send(120)

    async def send(self, ttl):
        if self.socket is None:
            return
        for address in self.addresses:
            self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
            packet = build_dns_response(build_announcement_records(
                self.name, SERVICE, self.name, address, self.port, self.properties, ttl=ttl))
            await asyncio.get_running_loop().sock_sendto(self.socket, packet, GROUP)

    async def set(self, enabled, properties):
        if enabled == self.enabled and properties == self.properties:
            return
        if self.enabled:
            try:
                await self.send(0)
            except OSError as error:
                self.network_error(error)
        self.enabled, self.properties = enabled, dict(properties)
        if enabled:
            try:
                await self.send(120)
            except OSError as error:
                self.network_error(error)

    def network_error(self, error):
        self.error = str(error)
        if self.socket:
            self.socket.close()
        self.socket = None

    async def listen(self):
        while True:
            try:
                if self.socket is None:
                    await asyncio.sleep(5)
                    await self.refresh_interfaces()
                    continue
                try:
                    packet, _ = await asyncio.wait_for(
                        asyncio.get_running_loop().sock_recvfrom(self.socket, 9000), 5)
                except TimeoutError:
                    await self.refresh_interfaces()
                    if self.enabled:
                        await self.send(120)
                    continue
                names = {name.rstrip('.').lower() for name, _ in _parse_query_questions(packet)}
                wanted = {SERVICE.rstrip('.'), (self.name + '.' + SERVICE).rstrip('.'), self.name + '.local'}
                if self.enabled and names & wanted:
                    await self.send(120)
            except OSError as error:
                self.network_error(error)

    async def close(self):
        if self.enabled:
            with contextlib.suppress(OSError):
                await self.send(0)
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.socket:
            self.socket.close()
