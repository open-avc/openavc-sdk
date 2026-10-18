"""Enforce manufacturer-selected Programmer and panel access."""

import asyncio
import hmac
import os
from contextlib import suppress
from pathlib import Path
from .host_policy import FileHostPolicy

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.routing import Match

from openavc.api.auth import (
    _basic, check_ws_auth, get_ws_auth_subprotocol,
    require_claimed_auth, require_programmer_auth,
)
from openavc.api.host_policy import interface_disabled_response, resolve_endpoint_policy
from openavc.utils.request_origin import cloud_session_secret, is_local_console_request

STATE = Path(os.environ["OPENAVC_INTERFACE_STATE"])
CLOSE = 4013


def authenticated(scope):
    conn = HTTPConnection(scope)
    return check_ws_auth(dict(conn.query_params), dict(conn.headers), cloud_secret=cloud_session_secret(conn))


class Policy:
    def __init__(self, app, identity):
        self.app = app
        self.identity = identity
        self.connections = {}
        self.control_token = os.environ.get("OPENAVC_HOST_CONTROL_TOKEN", "")
        self.host_policy = FileHostPolicy(STATE, identity, self.control_token)

    def state(self):
        return self.host_policy.settings()

    async def authenticate_body(self, scope):
        """Run declared canonical guards before FastAPI parses protected bodies."""
        headers = HTTPConnection(scope).headers
        if not headers.get("transfer-encoding") and headers.get("content-length", "0") == "0":
            return
        for route in self.app.routes:
            match, child = route.matches(scope)
            if match != Match.FULL:
                continue
            dependant = getattr(route, "dependant", None)
            pending = list(dependant.dependencies) if dependant is not None else []
            request = Request({**scope, **child})
            credentials = await _basic(request)
            while pending:
                dependency = pending.pop(0)
                if dependency.call in {require_programmer_auth, require_claimed_auth}:
                    await dependency.call(request, credentials=credentials)
                pending[0:0] = dependency.dependencies
            return

    def category(self, scope):
        for route in self.app.routes:
            match, child = route.matches(scope)
            if match != Match.FULL:
                continue
            plugin_surface = getattr(route, "_sdk_plugin_surface", None)
            if plugin_surface:
                surface, plugin_id = plugin_surface
                return "runtime" if surface == "guest" else ("plugin_ext", plugin_id)
            endpoint = getattr(route, "endpoint", None)
            module = getattr(endpoint, "__module__", "")
            name = getattr(endpoint, "__name__", "")
            key = module.removeprefix("openavc.api.") + ":" + name
            if key == "openavc_oem.host_policy:apply_policy":
                return "host"
            if key in {
                "routes.system:health_check", "routes.system:startup_status",
                "routes.push:device_push", "isc_ws:isc_websocket_endpoint",
            }:
                return "runtime"
            if key in {
                "panel_access:panel_access_check_in", "panel_access:panel_access_claim",
                "routes.project:panel_unlock",
            }:
                return "panel"
            if key in {
                "assets:serve_asset", "themes:get_theme",
                "routes.ui_files:serve_ui_file", "plugins:get_all_extensions",
                "plugins:serve_plugin_panel_file", "plugins:serve_plugin_file",
            }:
                return "render"
            if key == "plugins:get_plugin_ext_token":
                return ("plugin_token", child["path_params"]["plugin_id"])
            if key == "ws:websocket_endpoint":
                client = HTTPConnection(scope).query_params.get("client", "panel")
                return client if client in {"programmer", "panel", "preview"} else "invalid"
            if getattr(route, "name", "") == "panel":
                path = scope["path"].rstrip("/")
                if path == "/panel" or path == "/panel/index.html":
                    query = HTTPConnection(scope).query_params
                    if query.get("edit") == "1" or query.get("preview") == "1":
                        return "editor_shell"
                    return "panel"
                return "render"
            return "programmer"
        # Let the canonical route declaration classify method refusals and
        # redirects too. Unmatched requests still use Programmer permission.
        declared = resolve_endpoint_policy({**scope, "app": self.app})
        return declared.resolve(scope).value

    def allowed(self, scope, category=None):
        state = self.state()
        category = category or self.category(scope)
        if isinstance(category, tuple):
            kind, plugin_id = category
            if kind == "plugin_token":
                return state["programmer"] if authenticated(scope) else state["panel"]
            from openavc.api.plugin_ext import (
                PLUGIN_TOKEN_HEADER, PLUGIN_TOKEN_QUERY, panel_path_allowed,
                verify_panel_token, verify_plugin_token,
            )
            conn = HTTPConnection(scope)
            token = conn.headers.get(PLUGIN_TOKEN_HEADER) or conn.query_params.get(PLUGIN_TOKEN_QUERY, "")
            if authenticated(scope) or (token and verify_plugin_token(token, plugin_id)):
                return state["programmer"]
            prefix = f"/api/plugins/{plugin_id}/ext"
            panel_path = panel_path_allowed(plugin_id, scope["method"], scope["path"][len(prefix):] or "/")
            if token and verify_panel_token(token, plugin_id):
                # When both interfaces are enabled, leave an out-of-scope
                # credential to the existing guard's ordinary 401 response.
                return state["panel"] and (panel_path or state["programmer"])
            # Keep the ordinary route's authentication failure for missing/bad
            # credentials, while a disabled surface is refused before dispatch.
            return state["programmer"] or (state["panel"] and panel_path)
        if category == "host":
            conn = HTTPConnection(scope)
            return (is_local_console_request(conn)
                    and not any(k.lower().startswith(("x-forwarded", "x-openavc")) for k in conn.headers)
                    and len(self.control_token) == 64
                    and len(conn.headers.getlist("authorization")) == 1
                    and hmac.compare_digest(
                        conn.headers.get("authorization", "").encode("utf-8"),
                        ("Bearer " + self.control_token).encode("utf-8")))
        if category == "runtime":
            return True
        if category == "render":
            return state["panel"] or state["programmer"]
        if category == "editor_shell":
            return state["programmer"]
        if category == "preview":
            return state["programmer"] and authenticated(scope)
        return bool(state.get(category, False))

    async def apply(self):
        # Response acknowledges connection teardown, not just a saved checkbox.
        affected = [
            (task, done) for task, (scope, done) in list(self.connections.items())
            if not self.allowed(scope)
        ]
        for task, _ in affected:
            task.set()
        if affected:
            await asyncio.wait_for(asyncio.gather(*(done.wait() for _, done in affected)), 5)
        await self.cloud_policy.wait_for_idle()
        return self.state()


class Interfaces:
    def __init__(self, app, policy):
        self.app, self.policy = app, policy

    async def http(self, scope, receive, send, category):
        if category in {"host", "runtime"}:
            return await self.app(scope, receive, send)
        revoked, done = asyncio.Event(), asyncio.Event()
        self.policy.connections[revoked] = (scope, done)
        started = ended = False

        async def guarded_receive():
            message = await receive()
            if not self.policy.allowed(scope, category):
                revoked.set()
                raise asyncio.CancelledError
            return message

        async def guarded_send(message):
            nonlocal started, ended
            if not self.policy.allowed(scope, category):
                revoked.set()
                raise asyncio.CancelledError
            await send(message)
            if message["type"] == "http.response.start":
                started = True
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                ended = True

        async def watch():
            while not revoked.is_set():
                if not self.policy.allowed(scope, category):
                    revoked.set()
                    break
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(revoked.wait(), 0.1)

        application = asyncio.create_task(self.app(scope, guarded_receive, guarded_send))
        watcher = asyncio.create_task(watch())
        try:
            await asyncio.wait({application, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if not application.done():
                application.cancel()
            with suppress(asyncio.CancelledError):
                await application
            if revoked.is_set() and not ended:
                if not started:
                    response = JSONResponse({"detail": "This interface is disabled in product management.", "code": "interface_disabled"}, status_code=403, headers={"Cache-Control": "no-store"})
                    await response(scope, receive, send)
                else:
                    # Stop an already-started stream; a new request is refused.
                    # Fixed-length responses cannot complete early: the server
                    # closes that transport on its content-length mismatch.
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            self.policy.connections.pop(revoked, None)
            done.set()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        category = self.policy.category(scope)
        if not self.policy.allowed(scope, category):
            if scope["type"] == "websocket":
                await receive()
                await send({"type": "websocket.accept", "subprotocol": get_ws_auth_subprotocol(HTTPConnection(scope).headers)})
                auth_required = category == "preview" and self.policy.state()["programmer"] and not authenticated(scope)
                await send({"type": "websocket.close", "code": 4014 if auth_required else CLOSE,
                            "reason": "Sign in to the Programmer to use Preview." if auth_required else "This interface is disabled in product management."})
            else:
                await interface_disabled_response(scope)(scope, receive, send)
            return
        if scope["type"] == "http":
            try:
                await self.policy.authenticate_body(scope)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
                return await response(scope, receive, send)
            return await self.http(scope, receive, send, category)

        revoked, done = asyncio.Event(), asyncio.Event()
        self.policy.connections[revoked] = (scope, done)
        accepted = closed = False

        async def guarded_send(message):
            nonlocal accepted, closed
            await send(message)
            if message["type"] == "websocket.accept":
                accepted = True
            if message["type"] == "websocket.close":
                closed = True

        async def guarded_receive():
            message = await receive()
            if not self.policy.allowed(scope, category):
                revoked.set()
                return {"type": "websocket.disconnect", "code": CLOSE}
            return message

        async def watch():
            while not revoked.is_set():
                if not self.policy.allowed(scope, category):
                    revoked.set()
                    break
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(revoked.wait(), 0.1)

        application = asyncio.create_task(self.app(scope, guarded_receive, guarded_send))
        watcher = asyncio.create_task(watch())
        try:
            await asyncio.wait({application, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if not application.done():
                application.cancel()
            with suppress(asyncio.CancelledError):
                await application
            # The inner connection may already be closing under core policy.
            # Let its cancellation/cleanup settle before deciding whether this
            # owner still owes a close; never cancel a close after counting it.
            if revoked.is_set() and accepted and not closed:
                await send({"type": "websocket.close", "code": CLOSE, "reason": "This interface is disabled in product management."})
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            self.policy.connections.pop(revoked, None)
            done.set()


def install(app, identity):
    policy = Policy(app, identity)
    from .cloud_policy import install as install_cloud_policy

    policy.cloud_policy = install_cloud_policy(policy)

    policy.host_policy.install_endpoint(app, policy.apply)
    app.add_middleware(Interfaces, policy=policy)
    return policy
