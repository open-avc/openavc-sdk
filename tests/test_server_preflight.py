"""A staged mismatch must refuse without touching active firmware or data."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
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
        return json.loads(result.stdout)

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


if __name__ == '__main__':
    unittest.main()
