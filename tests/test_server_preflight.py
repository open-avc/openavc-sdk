"""A staged mismatch must refuse without touching active firmware or data."""
import hashlib
import importlib.util
import json
import os
import py_compile
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch
from zipfile import ZipFile

from openavc_oem.preflight import check
import openavc_oem.preflight as preflight


class ServerPreflight(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # macOS's default temporary directory passes through /var -> /private/var.
        # The source checker deliberately refuses symlinked snapshot ancestors.
        self.root = Path(self.temporary.name).resolve()
        self.bundle = self.root / 'bundle'
        (self.bundle / 'wheels').mkdir(parents=True)
        self.active = self.root / 'active'
        self.active.mkdir()
        (self.active / 'identity').write_text('existing-unit')
        (self.active / 'settings.json').write_text('{"programmer":false,"revision":8}')
        self.before = self.snapshot(self.active)
        self.packages = {}
        self.record = {'core': {'core_head': 'a' * 40}, 'wheels': {}}
        for distribution, package in [('openavc', 'openavc'), ('openavc-oem', 'openavc_oem')]:
            root = self.root / 'candidate' / package
            root.mkdir(parents=True)
            contents = {'__init__.py': '__version__ = "0.0.0"\n', 'policy.py': 'allowed = False\n'}
            wheel_path = self.bundle / 'wheels' / (package + '-0.0.0-py3-none-any.whl')
            with ZipFile(wheel_path, 'w') as wheel:
                wheel.writestr(package + '-0.0.0.dist-info/METADATA',
                               'Name: ' + distribution + '\nVersion: 0.0.0\n')
                for name, value in contents.items():
                    wheel.writestr(package + '/' + name, value)
                    (root / name).write_text(value)
            self.packages[package] = root
            self.record['wheels'][wheel_path.name] = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
        self.save_record()
        self.locator = patch('openavc_oem.preflight.package_root', side_effect=self.packages.__getitem__)
        self.locator.start()
        self.addCleanup(self.locator.stop)

    def snapshot(self, root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob('*') if path.is_file()}

    def tearDown(self):
        self.assertEqual(self.snapshot(self.active), self.before)

    def save_record(self):
        (self.bundle / 'bundle.json').write_text(json.dumps(self.record))

    def test_matching_payloads_report_the_selected_core_and_actual_locations(self):
        report = check(self.bundle, 'a' * 40)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['core_commit'], 'a' * 40)
        for name, root in self.packages.items():
            self.assertEqual(report['packages'][name]['origin'], str(root))
            self.assertEqual(report['packages'][name]['checked_files'], 2)

    def test_same_version_with_different_sdk_or_core_bytes_is_refused(self):
        for package in self.packages:
            with self.subTest(package=package):
                source = self.packages[package] / 'policy.py'
                original = source.read_bytes()
                source.write_text('allowed = True\n')
                with self.assertRaisesRegex(ValueError, 'files differ'):
                    check(self.bundle, 'a' * 40)
                source.write_bytes(original)

    def test_missing_or_leftover_importable_files_are_refused(self):
        root = self.packages['openavc_oem']
        for name in ['removed_module.py', 'removed_module.pyc']:
            with self.subTest(name=name):
                extra = root / name
                extra.write_bytes(b'old module')
                with self.assertRaisesRegex(ValueError, 'files differ'):
                    check(self.bundle, 'a' * 40)
                extra.unlink()
        (root / 'policy.py').unlink()
        with self.assertRaisesRegex(ValueError, 'files differ'):
            check(self.bundle, 'a' * 40)

    def test_wrong_core_and_modified_wheel_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'selected core commit'):
            check(self.bundle, 'b' * 40)
        wheel = next((self.bundle / 'wheels').glob('*.whl'))
        wheel.write_bytes(wheel.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'wheel hash'):
            check(self.bundle, 'a' * 40)

    def test_repeated_distribution_and_missing_integration_are_refused(self):
        name = next(name for name in self.record['wheels'] if name.startswith('openavc_oem'))
        duplicate = 'duplicate-0.0.0-py3-none-any.whl'
        (self.bundle / 'wheels' / duplicate).write_bytes((self.bundle / 'wheels' / name).read_bytes())
        self.record['wheels'][duplicate] = self.record['wheels'][name]
        self.save_record()
        with self.assertRaisesRegex(ValueError, 'repeats'):
            check(self.bundle, 'a' * 40)
        del self.record['wheels'][duplicate]
        del self.record['wheels'][name]
        self.save_record()
        with self.assertRaisesRegex(ValueError, 'both OpenAVC'):
            check(self.bundle, 'a' * 40)

    def setup_source_fixture(self):
        # Use the installed pinned core's actual pure evaluator in a separate
        # process. Only the qualification data and source are invented here.
        from openavc.drivers import setup_preflight, setup_compatibility, spec

        for module in (setup_preflight, setup_compatibility, spec):
            target = self.packages['openavc'] / 'drivers' / Path(module.__file__).name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(Path(module.__file__).read_bytes())
        (self.packages['openavc'] / 'drivers/__init__.py').write_text('')
        (self.packages['openavc_oem'] / 'preflight.py').write_bytes(Path(preflight.__file__).read_bytes())
        source = self.root / 'installed.py'
        source.write_text('raise AssertionError("installed source must not execute")\n')
        resource = {'schema': 'setup-compatibility-resource/v1', 'source_revision': 'b' * 40,
                    'descriptors': [], 'source_coverage': [{
                        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                        'driver_id': 'acme', 'driver_version': '1.0', 'declaration_sha256': 'c' * 64,
                        'actions': [{'action_id': 'pair', 'action_sha256': 'd' * 64,
                                     'result_contract': 'setup/v1'}]}]}
        resource_path = self.packages['openavc'] / 'drivers/setup-compatibility.json'
        resource_path.write_text(json.dumps(resource))
        self.repack_candidates()
        return source, resource, resource_path

    def repack_candidates(self):
        for distribution, package in [('openavc', 'openavc'), ('openavc-oem', 'openavc_oem')]:
            path = self.bundle / 'wheels' / (package + '-0.0.0-py3-none-any.whl')
            with ZipFile(path, 'w') as wheel:
                wheel.writestr(package + '-0.0.0.dist-info/METADATA', 'Name: ' + distribution + '\n')
                for source in self.packages[package].rglob('*'):
                    if source.is_file() and '__pycache__' not in source.parts:
                        wheel.write(source, package + '/' + source.relative_to(self.packages[package]).as_posix())
            self.record['wheels'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.save_record()

    def source_cli(self, source, expected):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('OPENAVC_', 'PYTHON'))}
        env.update(PYTHONPATH=str(self.root / 'candidate'),
                   OPENAVC_DATA_DIR=str(self.active), OPENAVC_LOG_DIR=str(self.active / 'logs'))
        result = subprocess.run([sys.executable, '-m', 'openavc_oem.preflight',
                                 '--bundle', str(self.bundle), '--expect-core', 'a' * 40,
                                 '--setup-source', str(source)],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report['activation_authorized'])
        return report

    def test_optional_sources_use_canonical_leaf_and_bind_checked_wheel(self):
        source, _, _ = self.setup_source_fixture()
        report = self.source_cli(source, 0)
        self.assertEqual(report['setup_sources']['status'], 'passed')
        self.assertFalse(report['activation_authorized'])
        self.assertFalse(report['setup_sources']['activation_authorized'])
        self.assertEqual(report['setup_sources']['target_sha256'], report['packages']['openavc']['wheel_sha256'])
        self.assertEqual(report['setup_sources']['scope'], 'supplied_primary_sources')
        source.write_text('raise AssertionError("unknown code must not execute either")\n')
        report = self.source_cli(source, 1)
        self.assertEqual(report['status'], 'refused')
        self.assertEqual(report['setup_sources']['sources'][0]['reason'], 'unknown_source')

    def test_package_mismatch_precedes_source_check_and_unmapped_actions_refuse(self):
        source, resource, path = self.setup_source_fixture()
        path.write_text('{}')
        refused = self.source_cli(source, 1)
        self.assertIn('files differ', refused['detail'])
        resource['source_coverage'][0]['actions'][0]['result_contract'] = None
        path.write_text(json.dumps(resource))
        self.repack_candidates()
        report = self.source_cli(source, 1)
        self.assertEqual(report['setup_sources']['sources'][0]['coverage']['unsupported_actions'], ['pair'])

    def test_missing_resource_does_not_pass_as_an_empty_installation(self):
        source, _, resource = self.setup_source_fixture()
        resource.unlink()
        self.repack_candidates()
        report = self.source_cli(source, 1)
        self.assertIn('no setup source qualification resource', report['detail'])

    def test_unchecked_bytecode_cannot_replace_verified_source(self):
        source, _, _ = self.setup_source_fixture()
        source.write_text('raise AssertionError("unknown installed code must not execute")\n')
        marker = self.root / 'cache-ran'
        replacement = self.root / 'replacement.py'
        replacement.write_text('from pathlib import Path\n'
                               f'Path({str(marker)!r}).write_text("unexpected execution")\n')
        for relative in ['__init__.py', 'drivers/__init__.py', 'drivers/spec.py',
                         'drivers/setup_compatibility.py', 'drivers/setup_preflight.py']:
            with self.subTest(module=relative):
                cache = Path(importlib.util.cache_from_source(str(self.packages['openavc'] / relative)))
                cache.parent.mkdir(exist_ok=True)
                py_compile.compile(str(replacement), cfile=str(cache), doraise=True,
                                   invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
                self.assertTrue(cache.is_file())
                report = self.source_cli(source, 1)
                self.assertEqual(report['setup_sources']['sources'][0]['reason'], 'unknown_source')
                self.assertFalse(marker.exists())

    def test_child_keeps_standard_library_ahead_of_candidate_packages(self):
        source, _, _ = self.setup_source_fixture()
        marker = self.root / 'shadow-ran'
        (self.root / 'candidate/argparse.py').write_text(
            'from pathlib import Path\n'
            f'Path({str(marker)!r}).write_text("unexpected execution")\n'
            'raise RuntimeError("candidate module shadowed the standard library")\n')
        report = check(self.bundle, 'a' * 40, setup_sources=[source])
        self.assertEqual(report['setup_sources']['status'], 'passed')
        self.assertFalse(marker.exists())

    def census_fixture(self):
        from openavc.drivers import setup_inventory, source_names
        import openavc_oem.setup_inventory as adapter
        source, _, _ = self.setup_source_fixture()
        core = self.packages['openavc']
        for module in (setup_inventory, source_names):
            (core / 'drivers' / Path(module.__file__).name).write_bytes(Path(module.__file__).read_bytes())
        (self.packages['openavc_oem'] / 'setup_inventory.py').write_bytes(Path(adapter.__file__).read_bytes())
        data = self.root / 'product-data'
        (data / 'server/driver_repo').mkdir(parents=True)
        (data / 'server/driver_repo/installed.py').write_bytes(source.read_bytes())
        old = self.root / 'old-package/openavc'
        for root in (old, core):
            (root / 'drivers/definitions').mkdir(parents=True, exist_ok=True)
            (root / 'templates').mkdir(exist_ok=True)
        self.repack_candidates()
        selection = adapter.FirmwareSetupSelection(
            '1' * 64,
            adapter.ServerSelection('2' * 64, '3' * 64, self.root / 'old-python', data, old, old.parent),
            adapter.ServerSelection('4' * 64, '5' * 64, Path(sys.executable), data, core, core.parent),
            (), '6' * 64)
        return selection, data / 'server/driver_repo/installed.py'

    def test_fixed_census_runs_in_the_checked_child_and_binds_payload_and_request(self):
        selection, source = self.census_fixture()
        selection = replace(selection, installed=replace(
            selection.installed, server_python=self.root / 'old-\u00e9-python'))
        with patch('openavc.drivers.setup_preflight.assess_capture', side_effect=AssertionError('parent evaluator')):
            report = check(self.bundle, 'a' * 40, setup_selection=selection)
        self.assertEqual(report['status'], 'assessed')
        self.assertFalse(report['activation_authorized'])
        self.assertFalse(report['installation_complete'])
        census = report['setup_inventory']
        self.assertEqual(census['sources']['source_eligibility'], 'passed')
        self.assertEqual(census['sources']['target_sha256'], report['packages']['openavc']['wheel_sha256'])
        self.assertEqual(census['evaluator']['core_module'], str(self.packages['openavc'] / 'drivers/setup_preflight.py'))
        source.write_bytes(b'unknown source must not execute')
        self.assertEqual(check(self.bundle, 'a' * 40, setup_selection=selection)['status'], 'refused')

    def test_census_rejects_wrong_candidate_before_reading_the_product(self):
        selection, _ = self.census_fixture()
        # Missing installed/data roots would fail capture; mismatch must be
        # detected first, without discovering or executing the old interpreter.
        installed = replace(selection.installed, product_data=self.root / 'missing-data')
        for field in ('package_root', 'app_root', 'server_python'):
            candidate = replace(selection.candidate, **{field: self.root})
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'selected candidate'):
                    check(self.bundle, 'a' * 40, setup_selection=replace(
                        selection, installed=installed, candidate=candidate))
        with self.assertRaisesRegex(ValueError, 'either'):
            check(self.bundle, 'a' * 40, setup_selection=selection, setup_sources=[])

    def test_census_child_does_not_search_unchecked_sibling_packages_or_modules(self):
        selection, _ = self.census_fixture()
        sdk_parent = self.root / 'separate-sdk'
        sdk_parent.mkdir()
        old_sdk = self.packages['openavc_oem']
        moved = sdk_parent / 'openavc_oem'
        old_sdk.rename(moved)
        self.packages['openavc_oem'] = moved
        marker = self.root / 'unchecked-import'
        code = f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\nraise AssertionError("unchecked import")\n'
        # Either parent ordering exposes an unchecked sibling. Neither parent
        # may become an import search root in the isolated child.
        for path in (old_sdk / '__init__.py', sdk_parent / 'openavc/__init__.py',
                     sdk_parent / 'json.py', selection.candidate.app_root / 'argparse.py'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code)
        report = check(self.bundle, 'a' * 40, setup_selection=selection)
        self.assertEqual(report['status'], 'assessed')
        self.assertEqual(report['setup_inventory']['evaluator']['sdk_module'], str(moved / 'setup_inventory.py'))
        self.assertFalse(marker.exists())

    def test_census_ignores_unchecked_sdk_and_core_bytecode(self):
        selection, _ = self.census_fixture()
        marker = self.root / 'unchecked-cache'
        replacement = self.root / 'cache-payload.py'
        replacement.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n')
        for path in (self.packages['openavc_oem'] / 'setup_inventory.py',
                     self.packages['openavc'] / 'drivers/setup_inventory.py'):
            cache = Path(importlib.util.cache_from_source(str(path)))
            cache.parent.mkdir(exist_ok=True)
            py_compile.compile(str(replacement), cfile=str(cache), doraise=True,
                               invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        report = check(self.bundle, 'a' * 40, setup_selection=selection)
        self.assertEqual(report['status'], 'assessed')
        self.assertFalse(marker.exists())

    def test_census_refuses_when_the_actual_child_report_exceeds_the_cap(self):
        selection, _ = self.census_fixture()
        with patch.object(preflight, '_REPORT_BYTES', 128):
            with self.assertRaisesRegex(ValueError, 'output byte limit'):
                check(self.bundle, 'a' * 40, setup_selection=selection)

    def selection_cli(self, payload, expected, *, extra=()):
        path = self.root / 'selection.json'
        path.write_bytes(payload)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('OPENAVC_', 'PYTHON'))}
        environment['PYTHONPATH'] = str(self.root / 'candidate')
        result = subprocess.run([sys.executable, '-m', 'openavc_oem.preflight',
                                 '--bundle', str(self.bundle), '--expect-core', 'a' * 40,
                                 '--setup-selection', str(path), *extra],
                                cwd=self.root, env=environment, capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), payload)
        if expected == 2:
            self.assertFalse(result.stdout)
            return result.stderr
        report = json.loads(result.stdout)
        self.assertIs(report['activation_authorized'], False)
        self.assertIs(report['installation_complete'], False)
        return report

    def test_selection_command_preserves_assessed_refused_and_package_dispositions(self):
        from openavc_oem.setup_inventory import selection_record
        selection, source = self.census_fixture()
        payload = json.dumps(selection_record(selection)).encode()
        report = self.selection_cli(payload, 0)
        self.assertEqual(report['status'], 'assessed')
        self.assertEqual(report['setup_inventory']['selection'], selection_record(selection))
        source.write_bytes(b'unknown source must not execute')
        report = self.selection_cli(payload, 1)
        self.assertEqual(report['status'], 'refused')
        self.assertEqual(report['setup_inventory']['sources']['source_eligibility'], 'refused')
        self.assertEqual(check(self.bundle, 'a' * 40)['status'], 'passed')

    def test_selection_command_refuses_malformed_oversized_and_ambiguous_modes(self):
        self.census_fixture()
        for payload in (b'{"schema":1,"schema":2}', b'{"activation_authorized":true}',
                        b'\xff', b' ' * 262145, b'[' * 10000 + b'0' + b']' * 10000):
            with self.subTest(length=len(payload)):
                self.assertEqual(self.selection_cli(payload, 1)['status'], 'refused')
        error = self.selection_cli(b'{}', 2, extra=('--setup-source', str(self.root / 'source.py')))
        self.assertIn('not allowed with argument', error)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO replacement control')
    def test_selection_command_checks_the_opened_object_before_reading(self):
        from openavc_oem.setup_inventory import selection_record
        selection, _ = self.census_fixture()
        path = self.root / 'selection.json'
        path.write_text(json.dumps(selection_record(selection)))
        marker = self.root / 'fifo-replaced'
        code = '''import os, pathlib, sys
from openavc_oem.preflight import main
original = os.open
selected, marker = pathlib.Path(sys.argv.pop(1)), pathlib.Path(sys.argv.pop(1))
def replace_at_open(path, flags, *args, **kwargs):
    if pathlib.Path(path) == selected:
        selected.unlink()
        os.mkfifo(selected)
        marker.write_text("replaced")
    return original(path, flags, *args, **kwargs)
os.open = replace_at_open
sys.exit(main())
'''
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('OPENAVC_', 'PYTHON'))}
        environment['PYTHONPATH'] = str(self.root / 'candidate')
        result = subprocess.run([sys.executable, '-c', code, str(path), str(marker),
                                 '--bundle', str(self.bundle), '--expect-core', 'a' * 40,
                                 '--setup-selection', str(path)], cwd=self.root,
                                env=environment, capture_output=True, text=True, timeout=5)
        self.assertEqual(marker.read_text(), 'replaced')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertIn('regular server selection file', report['detail'])
        self.assertFalse(report['activation_authorized'])
        self.assertFalse(report['installation_complete'])


class EvaluatorTransport(unittest.TestCase):
    def run_child(self, code, payload=None, *, output_limit=16384, error_limit=4096, timeout=5):
        children = []
        original = subprocess.Popen
        threads_before = set(threading.enumerate())

        def tracked(*args, **kwargs):
            process = original(*args, **kwargs)
            children.append(process)
            return process

        try:
            with patch.object(preflight, '_REPORT_BYTES', output_limit), \
                    patch.object(preflight, '_ERROR_BYTES', error_limit), \
                    patch.object(preflight, '_CHILD_TIMEOUT', timeout), \
                    patch.object(preflight.subprocess, 'Popen', side_effect=tracked):
                return preflight._run_evaluator([sys.executable, '-I', '-S', '-c', code], payload)
        finally:
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())
            self.assertTrue(children[0].stdin.closed)
            self.assertTrue(children[0].stdout.closed)
            self.assertTrue(children[0].stderr.closed)
            self.assertEqual(set(threading.enumerate()), threads_before)

    def test_duplex_request_and_exact_output_limit_preserve_utf8(self):
        payload = '\u00e9' * 100000
        result = self.run_child(
            'import sys; sys.stderr.buffer.write(b"x" * 4096); '
            'sys.stderr.flush(); data=sys.stdin.buffer.read(); '
            'sys.stdout.buffer.write(data)', payload, output_limit=len(payload.encode()))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, payload)
        self.assertEqual(result.stderr, 'x' * 4096)

    def test_stdout_and_stderr_overflow_refuse_and_reap(self):
        for stream in ('stdout', 'stderr'):
            with self.subTest(stream=stream):
                with self.assertRaisesRegex(ValueError, 'output byte limit'):
                    self.run_child(f'import sys,time; sys.{stream}.buffer.write(b"x" * 65536); '
                                   f'sys.{stream}.flush(); time.sleep(5)')

    def test_timeout_refuses_and_reaps_even_with_unconsumed_request(self):
        with self.assertRaisesRegex(ValueError, 'time limit'):
            self.run_child('import time; time.sleep(5)', 'x' * 262144, timeout=0.2)

    def test_invalid_encoding_refuses_and_nonzero_result_is_preserved(self):
        with self.assertRaisesRegex(ValueError, 'invalid UTF-8'):
            self.run_child('import sys; sys.stdout.buffer.write(b"\\xff")')
        result = self.run_child('import sys; print("refused"); sys.exit(1)')
        self.assertEqual((result.returncode, result.stdout), (1, 'refused\n'))


if __name__ == '__main__':
    unittest.main()
