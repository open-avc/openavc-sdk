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
        self.receiving = None
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
            await self.close_socket()
        self.addresses = addresses
        if not addresses:
            self.error = 'No IPv4 interface is available for equipment discovery.'
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
        sock = self.socket
        if sock is None:
            return
        for address in self.addresses:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
            packet = build_dns_response(build_announcement_records(
                self.name, SERVICE, self.name, address, self.port, self.properties, ttl=ttl))
            await asyncio.get_running_loop().sock_sendto(sock, packet, GROUP)

    async def set(self, enabled, properties):
        if enabled == self.enabled and properties == self.properties:
            return
        if self.enabled:
            try:
                await self.send(0)
            except OSError as error:
                await self.network_error(error)
        self.enabled, self.properties = enabled, dict(properties)
        if enabled:
            try:
                await self.send(120)
            except OSError as error:
                await self.network_error(error)

    async def close_socket(self):
        sock, self.socket = self.socket, None
        receiving = self.receiving
        if receiving is not None and not receiving.done():
            receiving.cancel()
            await asyncio.gather(receiving, return_exceptions=True)
        # Unregister the pending receive before its descriptor can be reused.
        if sock is not None:
            sock.close()

    async def network_error(self, error):
        self.error = str(error)
        await self.close_socket()

    async def listen(self):
        while True:
            try:
                if self.socket is None:
                    await asyncio.sleep(5)
                    await self.refresh_interfaces()
                    continue
                receiving = self.receiving = asyncio.create_task(
                    asyncio.get_running_loop().sock_recvfrom(self.socket, 9000))
                try:
                    packet, _ = await asyncio.wait_for(receiving, 5)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    continue
                except TimeoutError:
                    await self.refresh_interfaces()
                    if self.enabled:
                        await self.send(120)
                    continue
                finally:
                    if self.receiving is receiving:
                        self.receiving = None
                names = {name.rstrip('.').lower() for name, _ in _parse_query_questions(packet)}
                wanted = {SERVICE.rstrip('.'), (self.name + '.' + SERVICE).rstrip('.'), self.name + '.local'}
                if self.enabled and names & wanted:
                    await self.send(120)
            except OSError as error:
                await self.network_error(error)

    async def close(self):
        if self.enabled:
            with contextlib.suppress(OSError):
                await self.send(0)
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await self.close_socket()
