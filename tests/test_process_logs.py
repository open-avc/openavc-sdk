"""Observe retained bytes, storage failure and child progress independently."""

import asyncio
import errno
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from openavc_oem.process_logs import ProcessLogs


class ProcessOutput(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logs = ProcessLogs(self.root)
        self.addAsyncCleanup(self.logs.close)

    async def child(self, name, code):
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', code,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        process.log_capture = asyncio.create_task(self.logs.capture(process.stdout, name))
        try:
            await asyncio.wait_for(process.wait(), 5)
            await self.logs.finish(process)
            self.assertEqual(process.returncode, 0)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def flushed(self):
        async with asyncio.timeout(5):
            while self.logs.queue.unfinished_tasks:
                await asyncio.sleep(.01)

    async def test_recent_output_rotates_and_new_generation_replaces_old_output(self):
        # Pace writes so this tests disk rotation rather than the overflow path.
        await self.child('core-0.log', 'import os,time\nfor i in range(80):\n'
                         ' os.write(1, bytes([i])*65536); time.sleep(.003)\n')
        await self.flushed()
        files = sorted(self.root.glob('core-0.log*'))
        self.assertEqual(len(files), 3)
        self.assertTrue(all(p.stat().st_size <= 1024 * 1024 for p in files))
        self.assertEqual((self.root / 'core-0.log').read_bytes()[-65536:], bytes([79])*65536)
        self.assertEqual(self.logs.dropped_bytes, 0)
        await self.child('core-0.log', 'import os; os.write(2, b"new generation\\n")')
        await self.flushed()
        self.assertEqual(list(self.root.iterdir()), [self.root / 'core-0.log'])
        self.assertEqual((self.root / 'core-0.log').read_bytes(), b'new generation\n')

    async def test_blocked_storage_does_not_block_the_child_or_grow_the_queue(self):
        entered, release = threading.Event(), threading.Event()
        append = self.logs._append

        def blocked(path, data):
            entered.set()
            release.wait(10)
            append(path, data)

        progress = self.root / 'child-completed'
        with patch.object(self.logs, '_append', blocked):
            try:
                await self.child('core-0.log', 'import os,pathlib; os.write(1, b"x"*8388608); '
                                 + 'pathlib.Path(' + repr(str(progress)) + ').write_text("done")')
                self.assertTrue(entered.is_set())
                self.assertEqual(progress.read_text(), 'done')
                self.assertLessEqual(self.logs.queue.qsize(), 16)
                self.assertGreater(self.logs.dropped_bytes, 6 * 1024 * 1024)
            finally:
                release.set()
                await self.flushed()

    async def test_failed_storage_is_reported_and_new_output_recovers(self):
        with patch.object(self.logs, '_append', side_effect=OSError(errno.ENOSPC, 'No space left on device')):
            await self.child('equipment-0.log', 'import os; os.write(1,b"x"*65536)')
            await self.flushed()
        self.assertIn('No space left', self.logs.error)
        self.assertEqual(self.logs.dropped_bytes, 65536)
        await self.child('equipment-0.log', 'print("storage available")')
        await self.flushed()
        self.assertEqual((self.root / 'equipment-0.log').read_text(), 'storage available\n')
