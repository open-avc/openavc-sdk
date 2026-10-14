"""Interrupted source downloads can recover without accepting different inputs."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('yocto_setup', ROOT / 'examples/linux/yocto/setup.py')
SETUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETUP)


class YoctoFetch(unittest.TestCase):
    def fetch(self, results):
        output = io.StringIO()
        with patch.object(SETUP.subprocess, 'run', side_effect=results) as run, \
                patch.object(SETUP.time, 'sleep') as sleep, contextlib.redirect_stdout(output):
            SETUP.fetch_revision(Path('/fixture/source'), 'a' * 40)
        return run, sleep, output.getvalue()

    def test_interrupted_transfer_retries_the_identical_revision_and_keeps_error(self):
        error = 'error: RPC failed; curl 56 Recv failure: Connection reset by peer\nfatal: early EOF\n'
        run, sleep, output = self.fetch([
            subprocess.CompletedProcess(['git'], 128, error),
            subprocess.CompletedProcess(['git'], 0, 'Fetched selected revision\n'),
        ])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0], run.call_args_list[1])
        self.assertEqual(run.call_args.args[0][-1], 'a' * 40)
        sleep.assert_called_once_with(2)
        self.assertIn(error, output)
        self.assertIn('(2/3)', output)

    def test_exhausted_transfer_is_still_a_failure(self):
        failure = subprocess.CompletedProcess(['git'], 128, 'error: RPC failed; curl 56 reset\n')
        with patch.object(SETUP.subprocess, 'run', return_value=failure) as run, \
                patch.object(SETUP.time, 'sleep') as sleep, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(subprocess.CalledProcessError):
                SETUP.fetch_revision(Path('/fixture/source'), 'a' * 40)
        self.assertEqual(run.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])

    def test_nontransient_refusals_stop_without_retry(self):
        for error in ('fatal: Authentication failed', 'server certificate verification failed',
                      'fatal: remote error: upload-pack: not our ref ' + 'a' * 40,
                      'fatal: unable to access: The requested URL returned error: 404'):
            with self.subTest(error=error), \
                    patch.object(SETUP.subprocess, 'run', return_value=subprocess.CompletedProcess(['git'], 128, error)) as run, \
                    patch.object(SETUP.time, 'sleep') as sleep, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):
                    SETUP.fetch_revision(Path('/fixture/source'), 'a' * 40)
                self.assertEqual(run.call_count, 1)
                sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
