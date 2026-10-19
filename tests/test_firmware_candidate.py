"""The reference owner's command boundary must not accept stale cache results."""
import importlib.util
import json
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'firmware_candidate_check', Path(__file__).resolve().parents[1] / 'examples/linux/candidate_check.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


class CandidateCommand(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.owner = candidate.CandidateCheck.__new__(candidate.CandidateCheck)
        self.owner.evidence = self.root
        self.owner.data = self.root
        self.owner.environment = {}

    def test_unchecked_cache_cannot_replace_the_selected_command_source(self):
        source = self.root / 'probe.py'
        source.write_text('import json\nprint(json.dumps({"status": "refused"}))\nraise SystemExit(1)\n')
        cache_source = self.root / 'cache_source.py'
        cache_source.write_text('import json\nprint(json.dumps({"status": "passed"}))\n')
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.parent.mkdir()
        py_compile.compile(str(cache_source), cfile=str(cache), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        code = 'import sys; sys.path.insert(0, sys.argv[1]); import probe'
        command = [sys.executable, '-I', '-B', '-c', code, str(self.root)]
        # Establish that the cache actually overrides these source bytes when
        # the owner's isolation is absent. No package outside this fixture moves.
        baseline = subprocess.run(command, capture_output=True, check=True, timeout=5)
        self.assertEqual(json.loads(baseline.stdout)['status'], 'passed')
        result = self.owner.invoke('cache-control', command)
        self.assertEqual(result['status'], 'refused')
        self.assertEqual(json.loads((self.root / 'cache-control.stdout').read_bytes())['status'], 'refused')

    def test_timeout_preserves_partial_output_and_command_before_refusal(self):
        original = subprocess.run

        def shorter_deadline(*args, **kwargs):
            kwargs['timeout'] = 2
            return original(*args, **kwargs)

        command = [sys.executable, '-I', '-B', '-c',
                   'import time; print("partial-result", flush=True); time.sleep(30)']
        with patch.object(candidate.subprocess, 'run', side_effect=shorter_deadline):
            with self.assertRaisesRegex(ValueError, 'timed out'):
                self.owner.invoke('timeout-control', command)
        self.assertEqual((self.root / 'timeout-control.stdout').read_bytes(), b'partial-result\n')
        self.assertEqual((self.root / 'timeout-control.stderr').read_bytes(), b'')
        retained = json.loads((self.root / 'timeout-control-command.json').read_bytes())
        self.assertEqual(retained[0], sys.executable)
        self.assertEqual(retained[-1], command[-1])


if __name__ == '__main__':
    unittest.main()
