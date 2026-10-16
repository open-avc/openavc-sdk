"""A failed periodic announcement must not terminate equipment discovery."""
import asyncio
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

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


if __name__ == '__main__':
    unittest.main()
