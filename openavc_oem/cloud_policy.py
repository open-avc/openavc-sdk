"""Apply product programming permissions to cloud requests and commits."""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass

from openavc.cloud.command_handler import CommandHandler
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.cloud.protocol import CONFIG_PUSH, extract_payload
from openavc.core.engine import Engine

_request = ContextVar("sdk_cloud_programming", default=None)


@dataclass
class ProgrammingRequest:
    revision: int
    active: bool = True


class CloudPolicy:
    def __init__(self, policy):
        self.policy = policy
        self.committing = set()

    def admit(self):
        state = self.policy.state()
        if not state["programmer"]:
            raise PermissionError(
                "Programming is turned off in this device's OpenAVC settings. "
                "Enable Programmer in product management, then try again."
            )
        return state["revision"]

    async def wait_for_idle(self):
        pending = list(self.committing)
        if pending:
            await asyncio.wait_for(asyncio.gather(*(done.wait() for done in pending)), 5)


def install(policy):
    boundary = CloudPolicy(policy)
    original_handle = CommandHandler.handle
    original_apply = Engine._apply_project_locked
    original_tool = AIToolHandler._execute_tool

    async def execute_tool(handler, request_id, tool_name, callback, tool_input):
        try:
            revision = boundary.admit()
        except PermissionError as exc:
            return await handler._send_result(request_id, False, error=str(exc))
        request = ProgrammingRequest(revision)
        token = _request.set(request)

        async def admitted_callback(value):
            # The canonical dispatcher acquires its project lock before this
            # callback. A queued tool must recheck permission after that wait.
            if boundary.admit() != request.revision:
                raise PermissionError('OpenAVC settings changed while this request was waiting. Try again.')
            done = asyncio.Event()
            boundary.committing.add(done)
            try:
                return await callback(value)
            finally:
                boundary.committing.discard(done)
                done.set()

        try:
            return await original_tool(handler, request_id, tool_name, admitted_callback, tool_input)
        finally:
            request.active = False
            _request.reset(token)

    async def handle(handler, message):
        if message.get("type") != CONFIG_PUSH:
            return await original_handle(handler, message)
        try:
            revision = boundary.admit()
        except PermissionError as exc:
            return await handler._send_result(extract_payload(message).get("request_id", ""), False, error=str(exc))
        request = ProgrammingRequest(revision)
        token = _request.set(request)
        try:
            return await original_handle(handler, message)
        finally:
            # Background runtime tasks may inherit the context. They must not
            # retain this request's authorization after its dispatch finishes.
            request.active = False
            _request.reset(token)

    async def apply(engine, *args, **kwargs):
        request = _request.get()
        if request is None or not request.active:
            return await original_apply(engine, *args, **kwargs)
        if boundary.admit() != request.revision:
            raise PermissionError(
                "OpenAVC settings changed while this project was waiting to apply. "
                "Check product management, then try again."
            )
        done = asyncio.Event()
        boundary.committing.add(done)
        try:
            return await original_apply(engine, *args, **kwargs)
        finally:
            boundary.committing.discard(done)
            done.set()

    CommandHandler.handle = handle
    Engine._apply_project_locked = apply
    AIToolHandler._execute_tool = execute_tool
    return boundary
