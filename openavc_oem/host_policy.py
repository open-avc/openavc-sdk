"""Connect product-owned settings to the canonical OpenAVC host policy."""
import asyncio
import hmac
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from openavc.api.host_policy import EndpointPolicy, configure_policy, policy_route
from openavc.core.host_policy import HostCategory, HostPolicy, PolicySnapshot
from openavc.utils.request_origin import is_local_console_request

from .state import read_state


class FileHostPolicy(HostPolicy):
    def __init__(self, state_path: Path, identity: str, control_token: str):
        super().__init__()
        self.state_path = state_path
        self.identity = identity
        self.control_token = control_token

    def settings(self):
        try:
            return read_state(self.state_path, self.identity)
        except (OSError, ValueError, KeyError):
            return {"programmer": False, "panel": False, "revision": None}

    def snapshot(self):
        state = self.settings()
        return PolicySnapshot(state["revision"], state["programmer"], state["panel"])

    async def authenticate_host(self, request: Request):
        if not (
            is_local_console_request(request)
            and not any(key.lower().startswith(("x-forwarded", "x-openavc")) for key in request.headers)
            and len(self.control_token) == 64
            and len(request.headers.getlist("authorization")) == 1
            and hmac.compare_digest(
                request.headers.get("authorization", "").encode("utf-8"),
                ("Bearer " + self.control_token).encode("utf-8"),
            )
        ):
            raise HTTPException(403, "Product management authorization is required.")

    async def acknowledge(self, drain_existing=None, timeout=5.0):
        """Acknowledge only after both interface and operation owners settle."""
        deadline = asyncio.get_running_loop().time() + timeout
        self.refresh()
        existing_observed = drain_existing is None
        if drain_existing is not None:
            try:
                await asyncio.wait_for(drain_existing(), max(0, timeout))
                existing_observed = True
            except TimeoutError:
                pass
        result = await self.apply_current(max(0, deadline - asyncio.get_running_loop().time()))
        observed = existing_observed and result.observed
        return JSONResponse({
            "revision": result.snapshot.revision,
            "programmer": result.snapshot.programmer_enabled,
            "panel": result.snapshot.panel_enabled,
            "observed": observed,
            "pending_connections": result.pending_connections,
            "pending_commits": result.pending_commits,
            "pending_existing_operations": not existing_observed,
        }, status_code=200 if observed else 503, headers={"Cache-Control": "no-store"})

    def install_endpoint(self, app, drain_existing=None):
        configure_policy(app, self)

        async def apply_policy():
            return await self.acknowledge(drain_existing)

        app.router.add_api_route(
            "/api/host/interfaces", apply_policy, methods=["POST"],
            route_class_override=policy_route(EndpointPolicy(HostCategory.HOST, self.authenticate_host)),
        )
