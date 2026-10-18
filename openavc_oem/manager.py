"""Product controls, equipment export and optional server supervision."""
import asyncio
import contextlib
import hmac
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import ssl
import sys
import time
from aiohttp import BasicAuth, ClientError, ClientSession, ClientTimeout, web
from .state import defaults, read_state
from .storage import Journal, save_json, sync_parent
from .native_hosting import NativeHosting
from .supervision import Supervision
from .process_logs import ProcessLogs
from .mdns import Advertiser
from ._protocol import MAX_MESSAGE_BYTES

class PolicySaveUncertain(OSError):
    """A saved policy has not completed all persistence barriers."""

class PolicyApplyPending(RuntimeError):
    """The server reports that saved interface settings are still applying."""

def durable(path, value):
    save_json(path, value)

def sync_directory(path):
    sync_parent(path / 'entry')

async def stop(process):
    if process is None or process.returncode is not None:
        return
    command = getattr(process, 'shutdown_command', None)
    if command and process.stdin:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            process.stdin.write(command)
            await process.stdin.drain()
    else:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 10)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError('A supervised process did not finish shutdown. Check product diagnostics.')

class ProductManager:
    @property
    def session_cookie(self):
        # Browsers share cookies across ports in the multi-product example.
        return "oem_session_" + hashlib.sha256(self.identity.encode()).hexdigest()[:24]

    def __init__(self):
        self.data_dir = Path(self.config["data_dir"]).resolve()
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.identity = self.config["identity"]
        self.state_path = self.data_dir / "management.json"
        self.initialized_path = self.data_dir / "management.initialized"
        self.pending_path = self.data_dir / "management.pending"
        self.storage_cleanup_pending = False
        self.policy_error = None
        try:
            if not self.initialized_path.exists():
                # Mark before creating state: an interrupted first initialization
                # must not later be mistaken for a clean factory provisioning.
                durable(self.initialized_path, {"identity": self.identity})
                if not self.state_path.exists():
                    durable(self.state_path, defaults(self.identity, initial=True))
            self.state = read_state(self.state_path, self.identity)
            if self.pending_path.exists():
                raise PolicySaveUncertain("Interrupted policy transaction requires explicit repair")
        except (OSError, ValueError) as exc:
            self.state = defaults(self.identity)
            self.policy_error = (
                "OpenAVC settings could not be confirmed on storage. Product controls remain available. Repair settings to continue."
                if isinstance(exc, PolicySaveUncertain)
                else "OpenAVC settings are damaged or missing. Product controls remain available. Repair settings to continue."
            )
            print(json.dumps({"management_failure": "policy_load", "error": str(exc)}), file=sys.stderr, flush=True)
        self.lock = asyncio.Lock()
        self.command_dispatch_lock = asyncio.Lock()
        self.command_dispatch_uncertain = False
        self.hardware_transition_blocked = False
        self.sessions = set()
        self.owned = []
        self.core = None
        self.core_listener = None
        self.core_ready = False
        self.core_responsive = None
        self.interfaces_observed = None
        self.retry_setting = None
        self.native = None
        self.mdns = None
        self.transition = "idle"
        self.error = self.policy_error
        self.native_token = secrets.token_hex(32)
        self.core_control_token = secrets.token_hex(32)
        self.core_present = bool(self.config.get("server_python"))
        self.http = None
        self.run_dir = self.data_dir / "logs"
        self.run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.process_logs = ProcessLogs(self.run_dir)
        self.health_journal = Journal(self.run_dir / 'health.jsonl')
        self.health_observation = None
        self.diagnostic_error = None

    def status(self):
        return {
            **self.state,
            "identity": self.identity,

            "server_installed": self.core_present,
            "example_server_port": self.config.get('example_server_port') if self.config.get('example_ui') else None,
            "server_running": self.core_ready
            and self.core is not None
            and self.core.returncode is None,
            "server_responsive": self.core_responsive if self.core and self.core.returncode is None else None,
            "health_observation": self.health_observation,
            "diagnostic_error": self.diagnostic_error or self.process_logs.error or (
                'Some process output could not be retained. Check diagnostic storage.'
                if self.process_logs.dropped_bytes else None),
            "process_logs": self.process_logs.status(),
            "discovery_error": self.mdns.error if self.mdns else None,
            "interfaces_observed": self.interfaces_observed
            if self.core and self.core.returncode is None
            else None,
            "retry_setting": self.retry_setting,
            "settings_valid": self.policy_error is None,
            "storage_cleanup_pending": self.storage_cleanup_pending,
            "warning": "Settings were saved, but storage cleanup is not confirmed. Retry settings before restarting this product." if self.storage_cleanup_pending else None,
            "transition": self.transition,
            "error": self.policy_error or self.error,
            "equipment": self.equipment_status(),
        }

    def equipment_status(self):
        # Saved local-server intent reserves the hardware across failed starts
        # and crashes. A running process also reserves it until shutdown ends.
        reserved = self.state["server"] or bool(self.core and self.core.returncode is None) or self.hardware_transition_blocked
        available = not reserved and self.policy_error is None
        return {
            "reserved_locally": reserved,
            "control_port": self.config.get("equipment_port", 9443),
            "discovery_active": available and self.state["discovery"] and bool(self.mdns and self.mdns.active),
            "remote_access_active": available and self.state["remote_access"],
        }

    def check_policy(self):
        if self.policy_error:
            return False
        try:
            if self.pending_path.exists() and not self.storage_cleanup_pending:
                raise PolicySaveUncertain("Interrupted policy transaction requires explicit repair")
            if read_state(self.state_path, self.identity) != self.state:
                raise ValueError("Management policy changed outside the supervisor")
        except (OSError, ValueError) as exc:
            self.policy_error = (
                "OpenAVC settings could not be confirmed on storage. Product controls remain available. Repair settings to continue."
                if isinstance(exc, PolicySaveUncertain)
                else "OpenAVC settings are damaged or missing. Product controls remain available. Repair settings to continue."
            )
            self.interfaces_observed = None
            print(json.dumps({"management_failure": "policy_check", "error": str(exc)}), file=sys.stderr, flush=True)
        return self.policy_error is None

    async def isolate_policy(self):
        await self.stop_core()
        if self.mdns:
            await self.mdns.set(False, {})

    def save_policy(self, value):
        # Persist the interruption marker before replacing the intent. A valid
        # new JSON file alone cannot authorize boot after a failed final sync.
        durable(self.pending_path, {"revision": value["revision"], "identity": self.identity})
        try:
            durable(self.state_path, value)
        except OSError as exc:
            raise PolicySaveUncertain(str(exc)) from exc
        # The intent is committed. Later marker cleanup cannot undo that fact.
        # This proof is process-local; boot still refuses every retained marker.
        self.storage_cleanup_pending = True
        try:
            self.finish_policy_cleanup()
        except OSError as exc:
            print(json.dumps({"management_failure": "committed_cleanup", "error": str(exc)}), file=sys.stderr, flush=True)

    def finish_policy_cleanup(self):
        self.pending_path.unlink(missing_ok=True)
        sync_directory(self.pending_path.parent)
        self.storage_cleanup_pending = False

    async def repair(self, request):
        self.authenticated(request)
        async with self.lock:
            if self.check_policy():
                raise web.HTTPConflict(text="OpenAVC settings are already valid.")
            await self.isolate_policy()
            try:
                if self.state_path.exists():
                    evidence = self.state_path.with_name(f"management-invalid-{time.time_ns()}.json")
                    with evidence.open("xb") as stream:
                        os.chmod(evidence, 0o600)
                        stream.write(self.state_path.read_bytes())
                        stream.flush()
                        os.fsync(stream.fileno())
                recovered = defaults(self.identity)
                durable(self.initialized_path, {"identity": self.identity})
                durable(self.state_path, recovered)
                if self.pending_path.exists():
                    pending_evidence = self.pending_path.with_name(f"management-pending-invalid-{time.time_ns()}.json")
                    os.replace(self.pending_path, pending_evidence)
                    sync_directory(self.pending_path.parent)
            except (OSError, ValueError) as exc:
                print(json.dumps({"management_failure": "repair", "error": str(exc)}), file=sys.stderr, flush=True)
                return web.json_response({"detail": "Could not repair OpenAVC settings. Check product storage and try again.", "state": self.status()}, status=507)
            self.state = recovered
            self.storage_cleanup_pending = False
            self.policy_error = self.error = self.retry_setting = None
            # Resetting a corrupt revision also invalidates stale browser writers.
            # Product identity remains unchanged.
            self.sessions.clear()
            token = secrets.token_urlsafe(32)
            self.sessions.add(token)
            response = web.json_response(self.status())
            response.set_cookie(self.session_cookie, token, httponly=True, samesite="Strict", path="/oem")
            return response

    async def launch(self, name, command, *, log_name=None, **kwargs):
        stdout = kwargs.pop("stdout", None)
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE if stdout is None else stdout,
            stderr=asyncio.subprocess.STDOUT if stdout is None else asyncio.subprocess.PIPE, **kwargs
        )
        reader = proc.stdout if stdout is None else proc.stderr
        proc.log_capture = asyncio.create_task(self.process_logs.capture(
            reader, log_name or f"{name}-{len(self.owned)}.log"))
        self.owned.append((name, proc))
        return proc

    def validate_server_start(self):
        if not self.check_policy():
            raise web.HTTPConflict(text=self.policy_error)
        if not self.core_present:
            raise web.HTTPConflict(
                text="This firmware does not include the OpenAVC server."
            )

    async def start_core(self):
        self.validate_server_start()
        if self.status()["server_running"]:
            return
        await self.stop_core()
        await self.confirm_hardware_idle()
        environment = {
            **{key: value for key, value in os.environ.items()
               if not key.startswith('OPENAVC_')},
            **self.config.get('server_environment', {}),
            'OPENAVC_DATA_DIR': str(self.data_dir / 'server'),
            'OPENAVC_PROJECT': str(self.data_dir / 'server/project.avc'),
            'OPENAVC_EQUIPMENT_MANIFEST': str(self.data_dir / 'equipment.json'),
            'OPENAVC_FIRMWARE_HOST': str(self.data_dir / 'firmware.json'),
            'OPENAVC_INTERFACE_STATE': str(self.state_path),
            'OPENAVC_DEVICE_ID': self.identity,
            'OPENAVC_HOST_CONTROL_TOKEN': self.core_control_token,
            'OPENAVC_ALLOW_ANONYMOUS': 'false',
            'OPENAVC_SERVICE_MANAGED': '1',
        }
        bindings = await self.prepare_endpoint(environment)
        try:
            listeners = self.reserve_listeners(bindings)
        except OSError as exc:
            raise web.HTTPServiceUnavailable(
                text="OpenAVC could not reserve its local connection. Check product diagnostics and the server's bind address and port, then retry."
            ) from exc
        self.native_listeners = listeners
        try:
            self.core = await self.launch(
                "core",
                [self.config["server_python"], "-m", "openavc_oem.server"],
                env={**environment, "OPENAVC_LISTEN_FD": ",".join(str(listener.fileno()) for listener in listeners)},
                cwd=str(self.data_dir),
                pass_fds=tuple(listener.fileno() for listener in listeners),
            )
            async with asyncio.timeout(45):
                while True:
                    if self.core.returncode is not None:
                        raise RuntimeError(
                            f"OpenAVC exited with status {self.core.returncode}"
                        )
                    try:
                        async with self.http.get(
                            self.core_url + "/api/health", ssl=self.core_tls(),
                            timeout=ClientTimeout(total=1),
                        ) as response:
                            if response.status == 200:
                                self.core_ready = True
                                self.core_responsive = True
                                return
                    except (ClientError, OSError, asyncio.TimeoutError, ValueError):
                        pass
                    await asyncio.sleep(0.25)
        except TimeoutError as exc:
            await self.stop_core()
            raise web.HTTPServiceUnavailable(text="OpenAVC did not start within 45 seconds. Check product diagnostics, then retry.") from exc
        except BaseException:
            await self.stop_core()
            raise

    async def stop_core(self):
        self.core_ready = False
        self.core_responsive = None
        self.interfaces_observed = None
        if self.core:
            await stop(self.core)
            self.core = None
        if self.core_listener:
            self.core_listener.close()
            self.core_listener = None

    async def confirm_hardware_idle(self):
        message = "OpenAVC could not confirm completion of an earlier hardware command. The server remains stopped. Check product diagnostics before retrying."
        # Saved intent/reservation rejects new remote commands. Holding this
        # gate also accounts for HTTP requests already dispatched to the native
        # endpoint; its pending count includes callbacks still waiting on IPC.
        try:
            async with asyncio.timeout(4):
                async with self.command_dispatch_lock:
                    if self.command_dispatch_uncertain:
                        raise web.HTTPServiceUnavailable(text=message)
                    while True:
                        async with self.http.get(
                            self.native_url + "/health",
                            headers={"Authorization": "Bearer " + self.native_token},
                            ssl=self.native_tls,
                            timeout=ClientTimeout(total=1),
                        ) as response:
                            response.raise_for_status()
                            health = await response.json()
                        if health.get("completion_uncertain") is not False:
                            raise web.HTTPServiceUnavailable(text=message)
                        if type(health.get("pending")) is not int:
                            raise web.HTTPServiceUnavailable(text=message)
                        if health["pending"] == 0:
                            return
                        await asyncio.sleep(0.025)
        except (ClientError, OSError, TimeoutError, ValueError) as exc:
            raise web.HTTPServiceUnavailable(text=message) from exc

    async def watch_core(self, stop_requested):
        previous_end, last_recorded = time.monotonic(), 0.0
        while not stop_requested.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_requested.wait(), 1)
            if stop_requested.is_set():
                return
            # Keep listener ownership stable throughout an authenticated probe.
            # A stop/restart cannot release the port between this check and I/O.
            async with self.lock:
                if not self.status()["server_running"]:
                    self.core_responsive = None
                    continue
                process = self.core
                start = time.monotonic()
                observation = {
                    'event': 'core_health', 'start_unix_time': time.time(),
                    'start_monotonic': start, 'interval_s': start - previous_end,
                    'timeout_s': 1, 'status': None, 'error_type': None,
                }
                try:
                    async with self.http.get(self.core_url + "/api/health", ssl=self.core_tls(), timeout=ClientTimeout(total=1)) as response:
                        responsive = response.status == 200
                        observation['status'] = response.status
                except (ClientError, OSError, TimeoutError, ValueError) as exc:
                    responsive = False
                    observation.update(error_type=type(exc).__name__, error=str(exc)[:512])
                previous_end = time.monotonic()
                observation.update(end_unix_time=time.time(), end_monotonic=previous_end,
                                   duration_s=previous_end - start, responsive=responsive,
                                   previous_responsive=self.core_responsive)
                self.health_observation = observation
                if (not responsive or responsive != self.core_responsive
                        or observation['duration_s'] >= .25 or observation['interval_s'] >= 2
                        or previous_end - last_recorded >= 60):
                    try:
                        self.health_journal.append(observation)
                        last_recorded = previous_end
                    except (OSError, ValueError) as exc:
                        # A diagnostics-storage problem must not interrupt control.
                        self.diagnostic_error = f'{type(exc).__name__}: {exc}'
                        print(json.dumps({'diagnostic_failure': self.diagnostic_error}), file=sys.stderr, flush=True)
                if process.returncode is not None or not self.core_ready:
                    self.core_responsive = None
                    continue
                self.core_responsive = responsive

    async def confirm_interfaces(self):
        self.interfaces_observed = None
        async with self.http.post(
            self.core_url + "/api/host/interfaces", ssl=self.core_tls(),
            headers={"Authorization": "Bearer " + self.core_control_token}
        ) as response:
            applied = await response.json()
            expected = {
                key: self.state[key] for key in ("revision", "programmer", "panel")
            }
            matches = not any(
                type(applied.get(key)) is not type(value) or applied.get(key) != value
                for key, value in expected.items()
            )
            if response.status == 503 and applied.get("observed") is False and matches:
                raise PolicyApplyPending(
                    "Settings were saved. OpenAVC is still applying access changes. Retry settings to confirm."
                )
            response.raise_for_status()
            if applied.get("observed") is not True:
                raise RuntimeError("OpenAVC has not finished applying the saved access settings.")
            if not matches:
                raise RuntimeError("OpenAVC did not acknowledge the saved settings.")
            self.interfaces_observed = expected

    async def login(self, request):
        if not self.config.get("example_ui", False):
            raise web.HTTPNotFound()
        if request.headers.get("Origin") != f"http://{request.host}":
            raise web.HTTPForbidden()
        value = await request.json()
        expected = self.config["login"]
        if not (
            hmac.compare_digest(str(value.get("username", "")).encode(), expected["username"].encode())
            and hmac.compare_digest(
                str(value.get("password", "")).encode(), expected["password"].encode()
            )
        ):
            raise web.HTTPUnauthorized(text="The username or password is incorrect.")
        if len(self.sessions) >= 16:
            raise web.HTTPServiceUnavailable(
                text="Close an existing management session."
            )
        token = secrets.token_urlsafe(32)
        self.sessions.add(token)
        response = web.json_response(self.status())
        response.set_cookie(
            self.session_cookie, token, httponly=True, samesite="Strict", path="/oem"
        )
        return response

    async def logout(self, request):
        self.authenticated(request)
        self.sessions.discard(request.cookies.get(self.session_cookie))
        response = web.json_response({'signed_out': True})
        response.del_cookie(self.session_cookie, path='/oem')
        return response

    async def settings(self, request):
        self.authenticated(request)
        if request.method == "GET":
            if not self.check_policy():
                async with self.lock:
                    await self.isolate_policy()
            return web.json_response(self.status())
        value = await request.json()
        async with self.lock:
            if not self.check_policy():
                await self.isolate_policy()
                return web.json_response({"detail": self.policy_error, "state": self.status()}, status=409)
            if value.get("revision") != self.state["revision"]:
                raise web.HTTPConflict(
                    text="Settings changed in another session. Refresh before saving."
                )
            field, enabled = value.get("field"), value.get("enabled")
            if (
                field
                not in {"equipment", "server", "discovery", "remote_access", "programmer", "panel"}
                or type(enabled) is not bool
            ):
                raise web.HTTPBadRequest(text="Choose a supported on/off setting.")
            self.transition = (
                "starting" if field == "server" and enabled else "applying"
            )
            self.error = None
            phase = "validate"
            try:
                if field == "server" and enabled:
                    self.validate_server_start()
                if self.storage_cleanup_pending:
                    phase = "cleanup"
                    self.finish_policy_cleanup()
                changes = {key: enabled for key in ("discovery", "remote_access")} if field == "equipment" else {field: enabled}
                next_state = {**self.state, **changes,
                              "revision": self.state["revision"] + any(self.state[key] != value for key, value in changes.items())}
                phase = "save"
                if next_state != self.state:
                    self.save_policy(next_state)
                self.state = next_state
                phase = "apply"
                if field == "server":
                    self.recovery.reset()
                    self.hardware_transition_blocked = True
                # Withdraw before starting; direct-address admission reads the
                # same saved intent even while this coroutine is awaiting I/O.
                await self.advertise()
                if field == "server":
                    if enabled:
                        await self.start_core()
                    else:
                        await self.stop_core()
                        await self.confirm_hardware_idle()
                if self.status()["server_running"]:
                    await self.confirm_interfaces()
                if field == "server":
                    self.hardware_transition_blocked = False
                await self.advertise()
                self.retry_setting = field if self.storage_cleanup_pending else None
            except Exception as exc:
                if phase == "apply":
                    self.retry_setting = field
                if isinstance(exc, web.HTTPException):
                    self.error, status = exc.text, exc.status
                elif isinstance(exc, PolicyApplyPending):
                    self.error, status = str(exc), 503
                elif phase == "cleanup":
                    self.retry_setting = field
                    self.error = "Could not confirm storage cleanup. Settings remain saved. Check product storage and retry settings."
                    status = 503
                elif phase == "save":
                    if isinstance(exc, PolicySaveUncertain) or self.pending_path.exists():
                        self.policy_error = "OpenAVC settings could not be confirmed on storage. Product controls remain available. Repair settings to continue."
                        self.interfaces_observed = None
                        await self.isolate_policy()
                        self.error = self.policy_error
                    else:
                        self.error = "Could not save OpenAVC settings. Check product storage and try again."
                    status = 507
                else:
                    self.error = "Settings were saved, but OpenAVC could not apply them. Check product diagnostics and retry."
                    status = 503
                print(
                    json.dumps(
                        {"management_failure": phase, "field": field, "error": str(exc)}
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                self.transition = "idle"
                return web.json_response(
                    {"detail": self.error, "state": self.status()}, status=status
                )
            finally:
                self.transition = "idle"
        return web.json_response(self.status())

    async def endpoint(self, request):
        self.authorize_endpoint(request)
        if request.path not in {
            "/health",
            "/v1/description",
            "/v1/snapshot",
            "/v1/changes",
            "/v1/command",
        } and not (
            request.path.startswith("/v1/commands/")
            and request.path.removeprefix("/v1/commands/").isdigit()
        ):
            raise web.HTTPNotFound()
        if request.method not in {"GET", "POST"}:
            raise web.HTTPMethodNotAllowed(request.method, ["GET", "POST"])
        body = await request.read()
        # Uploading a body can span a local-server start or access change.
        # Authorize against current policy at dispatch, not only at arrival.
        self.authorize_endpoint(request)
        if request.path == "/v1/command" and request.method == "POST":
            async with self.command_dispatch_lock:
                self.authorize_endpoint(request)
                try:
                    return await self.forward_endpoint(request, body)
                except BaseException:
                    # A broken HTTP exchange does not establish whether the
                    # native endpoint accepted the submitted hardware work.
                    self.command_dispatch_uncertain = True
                    raise
        return await self.forward_endpoint(request, body)

    async def forward_endpoint(self, request, body):
        async with self.http.request(
            request.method,
            self.native_url + request.rel_url.path_qs,
            data=body,
            headers={
                "Authorization": "Bearer " + self.native_token,
                "X-OpenAVC-Client": request.headers.get("X-OpenAVC-Client", ""),
                "Content-Type": "application/json",
            },
            ssl=self.native_tls,
        ) as response:
            return web.Response(
                status=response.status,
                body=await response.read(),
                content_type="application/json",
            )

    def authenticated(self, request):
        expected = 'Bearer ' + self.management_token
        if (request.remote in {'127.0.0.1', '::1'}
                and len(request.headers.getall('Authorization', [])) == 1
                and hmac.compare_digest(request.headers.get('Authorization', '').encode(), expected.encode())):
            return
        if not self.config.get('example_ui', False) or request.cookies.get(self.session_cookie) not in self.sessions:
            raise web.HTTPUnauthorized(text='Authenticate through the product management backend.')
        if request.method != 'GET' and request.headers.get('Origin') != f'http://{request.host}':
            raise web.HTTPForbidden(text='Use this product’s management page.')

    def authorize_endpoint(self, request):
        def refused(status, message):
            raise status(text=json.dumps({'message': message}), content_type='application/json')
        if not self.check_policy():
            refused(web.HTTPServiceUnavailable, 'OpenAVC settings require repair in product management.')
        if self.equipment_status()['reserved_locally']:
            refused(web.HTTPForbidden, 'Remote control is paused for OpenAVC on this product. Check its status in the product settings.')
        try:
            credential = BasicAuth.decode(request.headers.get('Authorization', ''))
            valid = (len(request.headers.getall('Authorization', [])) == 1
                     and self.credential_check(credential.login, credential.password))
        except ValueError:
            valid = False
        if not valid:
            refused(web.HTTPUnauthorized, 'Enter this product’s username and password in its connection settings.')
        if not self.state['remote_access']:
            refused(web.HTTPForbidden, 'Remote device access is disabled in product management.')

    async def advertise(self):
        if self.mdns:
            model = self.model
            await self.mdns.set(not self.equipment_status()['reserved_locally'] and self.policy_error is None and self.state['discovery'], {
                'identity': self.identity, 'manufacturer': model['manufacturer'],
                'model': model['name'], 'remote': str(self.equipment_status()['remote_access_active']).lower(),
            })

    async def diagnostics(self, request):
        self.authenticated(request)
        return web.json_response({'status': self.status()}, headers={'Cache-Control': 'no-store'})

    async def run(self):
        stop_requested = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stop_requested.set)
        runners = []
        health_task = None
        self.http = ClientSession(timeout=ClientTimeout(total=8))
        try:
            self.native = await self.launch('equipment', [self.config['device_service'],
                self.config['model'], self.config['certificate'], self.config['bridge_path']],
                env={**os.environ, 'OPENAVC_TOKEN': self.native_token, 'OPENAVC_DEVICE_ID': self.identity},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            self.native.shutdown_command = b'quit\n'
            ready = (await asyncio.wait_for(self.native.stdout.readline(), 5)).decode().split()
            if len(ready) != 2 or ready[0] != 'READY' or not ready[1].isdigit():
                raise RuntimeError('The equipment service did not start. Read logs/equipment-0.log.')
            self.native_url = 'https://127.0.0.1:' + ready[1]
            self.native_tls = ssl.create_default_context(cafile=self.config['ca_file'])
            if self.core_present:
                (self.data_dir / 'server').mkdir(mode=0o700, exist_ok=True)
                durable(self.data_dir / 'equipment.json', {
                    'device_id': 'local', 'identity': self.identity, 'integration': self.model['id'],
                    'name': self.model['name'], 'connection': {'url': self.native_url,
                        'token': self.native_token, 'ca_file': self.config['ca_file']}})
                durable(self.data_dir / 'firmware.json', {'schema': 1, 'update_owner': 'firmware',
                    'firmware_version': self.config['firmware_version']})
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(self.config['certificate'])
            endpoints = web.Application(client_max_size=MAX_MESSAGE_BYTES)
            endpoints.router.add_route('*', '/v1/{tail:.*}', self.endpoint)
            endpoints.router.add_get('/health', self.endpoint)
            management = web.Application(client_max_size=16384)
            management.router.add_route('*', '/oem/settings', self.settings)
            management.router.add_post('/oem/repair', self.repair)
            management.router.add_get('/oem/diagnostics', self.diagnostics)
            management.router.add_post('/oem/login', self.login)
            management.router.add_post('/oem/logout', self.logout)
            if self.config.get('example_ui', False):
                management.router.add_get('/oem/', lambda _: web.FileResponse(Path(__file__).with_name('example.html')))
            for app, host, port, context in [
                (endpoints, self.config.get('equipment_bind', '0.0.0.0'), self.config.get('equipment_port', 9443), tls),
                (management, self.config.get('example_bind', '127.0.0.1') if self.config.get('example_ui') else '127.0.0.1', self.config.get('management_port', 9081), None),
            ]:
                runner = web.AppRunner(app, access_log=None, auto_decompress=False)
                await runner.setup()
                runners.append(runner)
                await web.TCPSite(runner, host, port, ssl_context=context).start()
            addresses = self.config.get('advertise_addresses')
            bind = self.config.get('equipment_bind', '0.0.0.0')
            if bind != '0.0.0.0':
                address = ipaddress.ip_address(bind)
                addresses = [] if address.version == 6 or address.is_loopback else [bind]
            self.mdns = Advertiser(self.identity, self.config.get('equipment_port', 9443), addresses)
            await self.mdns.start()
            if self.state['server']:
                try:
                    await self.start_core()
                    await self.confirm_interfaces()
                except Exception as error:
                    self.error = 'OpenAVC could not start. Check product diagnostics, then retry.'
                    print(str(error), file=sys.stderr, flush=True)
            await self.advertise()
            durable(self.data_dir / 'status.json', self.status())
            print('READY', flush=True)
            health_task = asyncio.create_task(self.watch_core(stop_requested))
            while not stop_requested.is_set():
                if not self.check_policy():
                    async with self.lock:
                        await self.isolate_policy()
                if self.native.returncode is not None:
                    raise RuntimeError('The equipment service stopped. Check product diagnostics.')
                await self.supervise(stop_requested)
                await asyncio.sleep(.1)
        finally:
            if health_task:
                health_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await health_task
            if self.mdns:
                await self.mdns.close()
            for runner in runners:
                await runner.cleanup()
            try:
                await self.stop_core()
                for _, process in reversed(self.owned):
                    await stop(process)
                    await self.process_logs.finish(process)
            finally:
                await self.http.close()
                await self.process_logs.close()


class Manager(NativeHosting, Supervision, ProductManager):
    def __init__(self, config, *, credential_check=None):
        self.config = dict(config)
        for key in ('identity', 'data_dir', 'model', 'management_token_file', 'device_service',
                    'bridge_path', 'certificate', 'ca_file'):
            if not isinstance(config.get(key), str) or not config[key].strip():
                raise ValueError(f'Configure a nonempty {key} in the product configuration.')
        for key in ('data_dir', 'model', 'management_token_file', 'device_service', 'bridge_path', 'certificate', 'ca_file'):
            if not Path(config[key]).is_absolute():
                raise ValueError(f'Use an absolute path for {key}.')
        for key in ('equipment_port', 'management_port'):
            if key in config and (type(config[key]) is not int or not 1 <= config[key] <= 65535):
                raise ValueError(f'{key} must be a port from 1 to 65535.')
        if config.get('server_python') and not config.get('firmware_version'):
            raise ValueError('Provide firmware_version when including the OpenAVC server.')
        if credential_check is None or config.get('example_ui'):
            login = config.get('login', {})
            if not all(isinstance(login.get(key), str) and login[key] for key in ('username', 'password')):
                raise ValueError('Provide product login credentials or a credential_check callback.')
        self.model = json.loads(Path(config['model']).read_text())
        self.management_token = Path(config['management_token_file']).read_text().strip()
        if len(self.management_token) < 32:
            raise ValueError('Provision a management credential with at least 32 random characters.')
        def configured_login(username, password):
            login = self.config['login']
            return (hmac.compare_digest(username.encode(), login['username'].encode())
                    and hmac.compare_digest(password.encode(), login['password'].encode()))
        self.credential_check = credential_check or configured_login
        super().__init__()
