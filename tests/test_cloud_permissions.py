"""Product access controls cover the maintained cloud dispatchers."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.cloud.command_handler import CommandHandler
from openavc.core.engine import Engine
from openavc_oem.cloud_policy import install


class Permissions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = {'revision': 1, 'programmer': True}
        self.policy = SimpleNamespace(state=lambda: dict(self.state))
        self.originals = (CommandHandler.handle, AIToolHandler._execute_tool, Engine._apply_project_locked)
        self.addCleanup(self.restore)
        self.boundary = install(self.policy)
        self.handler = AIToolHandler(Mock(), Mock(), Mock())
        self.handler._send_result = AsyncMock()

    def restore(self):
        CommandHandler.handle, AIToolHandler._execute_tool, Engine._apply_project_locked = self.originals

    async def test_disabled_programmer_refuses_ai_before_dispatch(self):
        self.state['programmer'] = False
        callback = AsyncMock()
        await self.handler._execute_tool('request', 'update_device', callback, {})
        callback.assert_not_awaited()
        self.handler._send_result.assert_awaited_once()
        self.assertFalse(self.handler._send_result.await_args.args[1])

    async def test_queued_ai_rechecks_after_its_project_lock(self):
        callback = AsyncMock()
        await self.handler._project_lock.acquire()
        task = asyncio.create_task(self.handler._execute_tool('request', 'update_device', callback, {}))
        await asyncio.sleep(0)
        self.state.update(revision=2, programmer=False)
        self.handler._project_lock.release()
        await task
        callback.assert_not_awaited()
        self.assertFalse(self.handler._send_result.await_args.args[1])

    async def test_ack_waits_for_an_admitted_write_without_cancelling_it(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def callback(value):
            entered.set()
            await release.wait()
            return {'saved': True}
        task = asyncio.create_task(self.handler._execute_tool('request', 'update_device', callback, {}))
        await entered.wait()
        self.state.update(revision=2, programmer=False)
        acknowledgement = asyncio.create_task(self.boundary.wait_for_idle())
        await asyncio.sleep(0)
        self.assertFalse(acknowledgement.done())
        release.set()
        await asyncio.gather(task, acknowledgement)
        self.handler._send_result.assert_awaited_once_with('request', True, result={'saved': True})

    async def test_default_enabled_policy_uses_the_normal_ai_callback(self):
        callback = AsyncMock(return_value={'saved': True})
        await self.handler._execute_tool('request', 'update_device', callback, {'name': 'Output'})
        callback.assert_awaited_once_with({'name': 'Output'})
        self.handler._send_result.assert_awaited_once_with('request', True, result={'saved': True})

    async def test_queued_config_push_cannot_apply_after_permission_changes(self):
        apply = AsyncMock()
        entered, release = asyncio.Event(), asyncio.Event()
        async def dispatch(handler, message):
            entered.set()
            await release.wait()
            await Engine._apply_project_locked(Mock(), Mock())
        self.restore()
        with patch.object(CommandHandler, 'handle', dispatch), patch.object(Engine, '_apply_project_locked', apply):
            boundary = install(self.policy)
            try:
                task = asyncio.create_task(CommandHandler.handle(Mock(), {'type': 'config_push'}))
                await entered.wait()
                self.state.update(revision=2, programmer=False)
                release.set()
                with self.assertRaises(PermissionError):
                    await task
                apply.assert_not_awaited()
                await boundary.wait_for_idle()
            finally:
                self.restore()


if __name__ == '__main__':
    unittest.main()
