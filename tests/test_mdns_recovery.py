"""A failed periodic announcement must not terminate equipment discovery."""
import asyncio
import socket
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from openavc_oem._dns_wire import encode_dns_name
from openavc_oem.mdns import Advertiser


class MulticastRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_withdrawal_still_disables_discovery(self):
        advertiser = Advertiser('example', 9443, [])
        advertiser.enabled = True
        original_socket = advertiser.socket = Mock()
        advertiser.send = AsyncMock(side_effect=PermissionError('network filtering'))
        await advertiser.set(False, {})
        self.assertFalse(advertiser.enabled)
        self.assertFalse(advertiser.active)
        self.assertIsNone(advertiser.socket)
        self.assertEqual(advertiser.error, 'network filtering')
        advertiser.send.assert_awaited_once_with(0)
        original_socket.close.assert_called_once()

    async def test_blocked_enable_keeps_intent_and_reports_unavailable_discovery(self):
        advertiser = Advertiser('example', 9443, [])
        original_socket = advertiser.socket = Mock()
        advertiser.send = AsyncMock(side_effect=PermissionError('network filtering'))
        await advertiser.set(True, {'model': 'invented'})
        self.assertTrue(advertiser.enabled)
        self.assertEqual(advertiser.properties, {'model': 'invented'})
        self.assertFalse(advertiser.active)
        self.assertIsNone(advertiser.socket)
        self.assertEqual(advertiser.error, 'network filtering')
        advertiser.send.assert_awaited_once_with(120)
        original_socket.close.assert_called_once()

    async def test_periodic_send_failure_reopens_the_socket_and_keeps_listening(self):
        advertiser = Advertiser('example', 9443, [])
        advertiser.enabled = True
        original_socket, restored_socket = Mock(), Mock()
        advertiser.socket = original_socket
        observed = []

        async def refresh():
            observed.append(advertiser.error)
            if advertiser.socket is None:
                advertiser.socket = restored_socket
                advertiser.error = None

        async def receive(_socket, _limit):
            raise TimeoutError()

        async def wait(awaitable, _timeout):
            return await awaitable

        advertiser.refresh_interfaces = refresh
        advertiser.send = AsyncMock(side_effect=[PermissionError('network filtering'), asyncio.CancelledError()])
        runtime = SimpleNamespace(get_running_loop=lambda: SimpleNamespace(sock_recvfrom=receive),
                                  wait_for=wait, sleep=AsyncMock(), create_task=asyncio.create_task,
                                  current_task=asyncio.current_task, gather=asyncio.gather,
                                  CancelledError=asyncio.CancelledError)
        with patch('openavc_oem.mdns.asyncio', runtime):
            with self.assertRaises(asyncio.CancelledError):
                await advertiser.listen()
        self.assertEqual(advertiser.send.await_count, 2)
        original_socket.close.assert_called_once()
        restored_socket.close.assert_not_called()
        self.assertIn('network filtering', observed)
        self.assertIsNone(advertiser.error)

    async def test_withdrawal_failure_cannot_break_a_connection_reusing_the_socket_descriptor(self):
        advertiser = Advertiser('example', 9443, [])
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(('127.0.0.1', 0))
        udp.setblocking(False)
        descriptor = udp.fileno()
        advertiser.socket = udp
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        receive = loop.sock_recvfrom

        async def observed(*args):
            entered.set()
            return await receive(*args)

        writer = outgoing = None
        with patch.object(loop, 'sock_recvfrom', observed):
            advertiser.task = asyncio.create_task(advertiser.listen())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                await advertiser.network_error(OSError('injected multicast send failure'))
                incoming, outgoing = socket.socketpair()
                self.assertEqual(incoming.fileno(), descriptor)
                reader, writer = await asyncio.open_connection(sock=incoming)
                outgoing.sendall(b'connection remains readable')
                self.assertEqual(await asyncio.wait_for(reader.readexactly(27), 1),
                                 b'connection remains readable')
                self.assertFalse(advertiser.task.done())
            finally:
                if outgoing is not None:
                    outgoing.close()
                if writer is not None:
                    writer.close()
                    await writer.wait_closed()
                await advertiser.close()

    async def test_a_finished_task_is_not_reported_as_active(self):
        advertiser = Advertiser('example', 9443, [])
        advertiser.enabled = True
        advertiser.socket = Mock()
        self.assertFalse(advertiser.active)
        advertiser.task = asyncio.create_task(asyncio.sleep(0))
        self.assertTrue(advertiser.active)
        await advertiser.task
        self.assertFalse(advertiser.active)


class RecordingSocket:
    """A UDP socket that keeps every option, bind and close it is given."""

    def __init__(self, *_args):
        self.calls = []
        self.interface = None

    def setsockopt(self, level, option, value):
        self.calls.append((level, option, value))
        if (level, option) == (socket.IPPROTO_IP, socket.IP_MULTICAST_IF):
            self.interface = socket.inet_ntoa(value)

    def bind(self, address):
        self.calls.append(('bind', address))

    def setblocking(self, _flag):
        pass

    def close(self):
        self.calls.append(('close',))

    def memberships(self):
        return [value for level, option, value in
                (call for call in self.calls if len(call) == 3)
                if (level, option) == (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP)]


class SelectedInterfaces(unittest.IsolatedAsyncioTestCase):
    """The socket binds every address so multicast arrives; it joins and answers only where chosen."""

    async def test_the_group_is_joined_and_announced_only_on_the_selected_addresses(self):
        advertiser = Advertiser('example', 9443, ['192.0.2.20', '192.0.2.10'])
        advertiser.enabled = True
        created, sent = [], []

        def make(*args):
            created.append(RecordingSocket(*args))
            return created[-1]

        async def sendto(sock, _packet, address):
            sent.append((sock.interface, address))

        with patch('openavc_oem.mdns.socket.socket', make), \
                patch.object(asyncio.get_running_loop(), 'sock_sendto', sendto):
            await advertiser.refresh_interfaces()
        self.assertIsNone(advertiser.error)
        [sock] = created
        self.assertIn(('bind', ('', 5353)), sock.calls)
        group = socket.inet_aton('224.0.0.251')
        self.assertEqual(sock.memberships(), [group + socket.inet_aton('192.0.2.10'),
                                              group + socket.inet_aton('192.0.2.20')])
        self.assertEqual(sent, [('192.0.2.10', ('224.0.0.251', 5353)),
                                ('192.0.2.20', ('224.0.0.251', 5353))])

    async def test_a_question_is_answered_on_the_group_never_to_the_asker(self):
        advertiser = Advertiser('example', 9443, ['192.0.2.10'])
        advertiser.enabled = True
        advertiser.addresses = ['192.0.2.10']
        advertiser.socket = RecordingSocket()
        question = (struct.pack('!HHHHHH', 0, 0, 1, 0, 0, 0)
                    + encode_dns_name('_openavc-device._tcp.local.') + struct.pack('!HH', 12, 1))
        waiting, sent = asyncio.Event(), []

        async def receive(_sock, _limit):
            if not waiting.is_set():
                waiting.set()
                return question, ('198.51.100.7', 5353)
            await asyncio.Event().wait()

        async def sendto(sock, _packet, address):
            sent.append((sock.interface, address))

        loop = asyncio.get_running_loop()
        with patch.object(loop, 'sock_recvfrom', receive), patch.object(loop, 'sock_sendto', sendto):
            listening = asyncio.create_task(advertiser.listen())
            await asyncio.wait_for(waiting.wait(), 1)
            for _ in range(20):
                if sent:
                    break
                await asyncio.sleep(0)
            listening.cancel()
            await asyncio.gather(listening, return_exceptions=True)
        self.assertEqual(sent, [('192.0.2.10', ('224.0.0.251', 5353))])


if __name__ == '__main__':
    unittest.main()
