"""A staged mismatch must refuse without touching active firmware or data."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from openavc_oem.preflight import check


class ServerPreflight(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
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


if __name__ == '__main__':
    unittest.main()
