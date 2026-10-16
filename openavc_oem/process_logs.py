"""Bound child output without making a child wait for diagnostic storage."""

import asyncio
from pathlib import Path
import queue
import threading


class ProcessLogs:
    """One storage worker, a 1 MiB queue and three 1 MiB files per output slot."""

    chunk_bytes = 64 * 1024
    segment_bytes = 1024 * 1024

    def __init__(self, directory):
        self.directory = Path(directory)
        self.queue = queue.Queue(maxsize=16)
        self.stopping = threading.Event()
        self.dropped_bytes = 0
        self.drop_lock = threading.Lock()
        self.error = None
        self.worker = threading.Thread(target=self._write, name='product-log-storage', daemon=True)
        self.worker.start()

    def _append(self, path, data):
        if path.exists() and path.stat().st_size + len(data) > self.segment_bytes:
            older = path.with_name(path.name + '.1')
            if older.exists():
                older.replace(path.with_name(path.name + '.2'))
            path.replace(older)
        with path.open('ab') as stream:
            path.chmod(0o600)
            stream.write(data)

    def _write(self):
        generations = {}
        while not self.stopping.is_set() or not self.queue.empty():
            try:
                name, generation, data = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                path = self.directory / name
                if data is None:
                    generations[name] = generation
                    for suffix in ('', '.1', '.2'):
                        path.with_name(path.name + suffix).unlink(missing_ok=True)
                elif generations.get(name) is generation:
                    self._append(path, data)
            except OSError as error:
                self.error = f'Could not save process output: {error}'
                self._drop(len(data) if data is not None else 0)
            finally:
                self.queue.task_done()

    async def capture(self, reader, name):
        generation = object()
        try:
            self.queue.put_nowait((name, generation, None))
            recording = True
        except queue.Full:
            recording = False
        while data := await reader.read(self.chunk_bytes):
            if not recording:
                try:
                    self.queue.put_nowait((name, generation, None))
                    recording = True
                except queue.Full:
                    pass
            if recording:
                try:
                    self.queue.put_nowait((name, generation, data))
                    continue
                except queue.Full:
                    pass
            self._drop(len(data))

    def _drop(self, count):
        with self.drop_lock:
            self.dropped_bytes += count

    async def finish(self, process):
        task = getattr(process, 'log_capture', None)
        if task is not None:
            try:
                await asyncio.wait_for(task, 2)
            except TimeoutError:
                self.error = 'Process output did not close after shutdown. Check product diagnostics.'

    def status(self):
        return {'dropped_bytes': self.dropped_bytes, 'queued_chunks': self.queue.qsize(),
                'error': self.error}

    async def close(self):
        self.stopping.set()
        await asyncio.to_thread(self.worker.join, 2)
        if self.worker.is_alive():
            self.error = 'Diagnostic storage did not finish writing before shutdown.'
