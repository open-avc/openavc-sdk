"""Bounded server restart supervision; accepted hardware work is never replayed."""

import asyncio
import contextlib
import time
from .storage import Journal

class RecoveryBudget:
    """Bound rapid failures, allowing a new budget after sustained operation."""

    def __init__(self, delay=5.0, attempts=3, stable=60.0):
        self.delay, self.limit, self.stable = delay, attempts, stable
        self.reset()

    def reset(self):
        self.attempts = 0
        self.next_at = None
        self.healthy_since = None

    def healthy(self, now):
        self.next_at = None
        if self.healthy_since is None:
            self.healthy_since = now
        if now - self.healthy_since >= self.stable:
            self.attempts = 0

    def due(self, now):
        self.healthy_since = None
        if self.attempts >= self.limit:
            return False
        if self.next_at is None:
            self.next_at = now + self.delay
        return now >= self.next_at

    def consume(self, now):
        self.attempts += 1
        self.next_at = now + self.delay

class Supervision:

    def __init__(self):
        super().__init__()
        self.recovery = RecoveryBudget()
        self.recovery_journal = Journal(self.run_dir / "recovery.jsonl")
        self.core_launches = 0


    def recovery_record(self, event, **facts):
        try:
            self.recovery_journal.append({"event": event, "monotonic": time.monotonic(), **facts})
        except OSError as exc:
            self.diagnostic_error = f"Could not save restart diagnostics: {exc}"


    async def launch(self, name, command, **kwargs):
        if name != "core":
            return await super().launch(name, command, **kwargs)
        # Four generation slots retain recent exits without accumulating file
        # descriptors, process objects or a new logfile on every restart.
        log = (self.run_dir / f"core-{self.core_launches % 4}.log").open("w")
        try:
            proc = await asyncio.create_subprocess_exec(*command, stdout=log, stderr=log, **kwargs)
        except BaseException:
            log.close()
            raise
        self.core_launches += 1
        proc.supervision_log = log
        self.logs.append(log)
        self.owned.append((name, proc))
        self.recovery_record("launched", pid=proc.pid, generation=self.core_launches)
        return proc


    async def stop_core(self):
        proc = self.core
        try:
            await super().stop_core()
        finally:
            if proc and proc.returncode is not None:
                self.recovery_record("reaped", pid=proc.pid, returncode=proc.returncode)
                self.owned[:] = [(name, child) for name, child in self.owned if child is not proc]
                log = getattr(proc, "supervision_log", None)
                if log:
                    log.close()
                    self.logs[:] = [item for item in self.logs if item is not log]


    async def supervise(self, stop_requested):
        if stop_requested.is_set() or self.transition != "idle":
            return
        async with self.lock:
            if stop_requested.is_set() or not self.state["server"] or not self.check_policy():
                self.recovery.reset()
                return
            if self.status()["server_running"]:
                if self.interfaces_observed is not None:
                    self.recovery.healthy(time.monotonic())
                return
            # Retire the exited child and its listener immediately, not only
            # when the next attempt starts. Saved server intent still reserves
            # the equipment throughout this interval.
            if self.core and self.core.returncode is not None:
                await self.stop_core()
            now = time.monotonic()
            if not self.recovery.due(now):
                if self.recovery.attempts >= self.recovery.limit:
                    self.error = "OpenAVC stopped repeatedly. Check product diagnostics, then retry starting it. Product controls remain available."
                    self.retry_setting = "server"
                else:
                    self.error = "OpenAVC stopped. Restarting automatically."
                return
            self.recovery.consume(now)
            self.transition = "starting"
            self.recovery_record("attempt", attempt=self.recovery.attempts)
            try:
                # start_core retains the policy validation, exclusive listener
                # and hardware-completion barrier used for a manual start.
                async def start():
                    await self.start_core()
                    await self.confirm_interfaces()

                starting = asyncio.create_task(start())
                stopping = asyncio.create_task(stop_requested.wait())
                try:
                    await asyncio.wait({starting, stopping}, return_when=asyncio.FIRST_COMPLETED)
                    if stop_requested.is_set():
                        starting.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await starting
                        await self.stop_core()
                        return
                    await starting
                finally:
                    for task in (starting, stopping):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(starting, stopping, return_exceptions=True)
                self.recovery.healthy(time.monotonic())
                self.error = None
                self.retry_setting = "server" if self.storage_cleanup_pending else None
                self.hardware_transition_blocked = False
                self.recovery_record("ready", pid=self.core.pid)
            except asyncio.CancelledError:
                await self.stop_core()
                raise
            except Exception as exc:
                await self.stop_core()
                self.recovery.next_at = time.monotonic() + self.recovery.delay
                self.recovery_record("failed", error=str(exc), error_type=type(exc).__name__)
                self.error = "OpenAVC could not restart. Check product diagnostics."
                self.retry_setting = "server"
            finally:
                self.transition = "idle"
                await self.advertise()
