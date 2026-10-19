"""A fixed source census preserves locations without granting firmware activation."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from openavc_oem.setup_inventory import FirmwareSetupSelection, ServerSelection, assess_server_selection


class SetupInventory(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.data = self.root / 'data'
        (self.data / 'server').mkdir(parents=True)
        selections = []
        for name in ('old', 'new'):
            app = self.root / name
            package = app / 'openavc'
            (package / 'drivers/definitions').mkdir(parents=True)
            (package / 'templates').mkdir()
            selections.append(ServerSelection('1' * 64, '2' * 64, app / 'bin/python',
                                               self.data, package, app))
        self.selection = FirmwareSetupSelection('3' * 64, *selections, (), '4' * 64)
        self.source = b'raise AssertionError("must never import")\n'
        self.source_sha = hashlib.sha256(self.source).hexdigest()
        self.resource = self.root / 'resource.json'
        self.resource.write_text(json.dumps({
            'schema': 'setup-compatibility-resource/v1', 'source_revision': 'a' * 40,
            'descriptors': [], 'source_coverage': [{
                'source_sha256': self.source_sha, 'driver_id': 'acme', 'driver_version': '1',
                'declaration_sha256': '5' * 64, 'actions': [],
            }],
        }))
        self.resource_sha = hashlib.sha256(self.resource.read_bytes()).hexdigest()

    def assess(self, selection=None):
        return assess_server_selection(selection or self.selection, resource=self.resource,
                                       expected_resource_sha256=self.resource_sha, target_sha256='6' * 64)

    def test_empty_and_yaml_only_still_observe_all_roles_without_installation_authority(self):
        result = self.assess()
        self.assertEqual(len(result['sources']['capture']['roots']), 10)
        self.assertEqual(result['sources']['source_eligibility'], 'empty')
        self.assertEqual(result['status'], 'assessed')
        self.assertTrue(result['profile_roles_observed'])
        self.assertFalse(result['installation_complete'])
        self.assertFalse(result['activation_authorized'])
        self.assertIn('installed_launch_association', result['unproven'])
        self.assertIn('recovery_destination_mapping', result['unproven'])
        (self.selection.candidate.package_root / 'drivers/definitions/widget.avcdriver').write_text('driver: acme')
        after = self.assess()
        self.assertEqual(after['sources']['source_eligibility'], 'empty')
        self.assertNotEqual(result['sources']['census_sha256'], after['sources']['census_sha256'])

    def test_populated_persistent_root_does_not_hide_retained_legacy_source(self):
        persistent = self.data / 'server/driver_repo'
        persistent.mkdir()
        (persistent / 'current.py').write_bytes(self.source)
        legacy = self.selection.installed.app_root / 'driver_repo'
        legacy.mkdir()
        (legacy / 'retained.py').write_bytes(b'unknown custom source')
        result = self.assess()
        self.assertEqual(result['status'], 'refused')
        self.assertEqual(result['sources']['capture']['source_count'], 3)
        self.assertEqual((legacy / 'retained.py').read_bytes(), b'unknown custom source')

    def test_inactive_library_all_templates_and_pending_imports_are_selected(self):
        library = self.data / 'server/saved_projects/inactive'
        library.mkdir(parents=True)
        template = self.selection.candidate.package_root / 'templates/future.zip'
        pending = self.root / 'pending.zip'
        for path in (library / 'bundle.zip', template, pending):
            with ZipFile(path, 'w') as archive:
                archive.writestr('drivers/widget.py', self.source)
        result = self.assess(replace(self.selection, pending_imports=(pending,)))
        self.assertEqual(result['sources']['source_eligibility'], 'passed')
        self.assertEqual(result['sources']['capture']['source_count'], 4)
        roles = {row['role'] for row in result['sources']['capture']['members']
                 if row['disposition'] == 'primary_python'}
        self.assertEqual(roles, {'installed_saved_library', 'candidate_saved_library',
                                'candidate_templates', 'pending_import_0'})

    def test_absent_optional_roots_require_parents_and_required_template_roots(self):
        (self.selection.candidate.package_root / 'templates').rmdir()
        with self.assertRaises(OSError):
            self.assess()

    def test_dangling_optional_directory_is_not_absence(self):
        (self.data / 'server/driver_repo').symlink_to(self.root / 'missing', target_is_directory=True)
        with self.assertRaises(OSError):
            self.assess()

    def test_changed_persistent_root_and_missing_server_are_not_empty_installs(self):
        different = self.root / 'different'
        different.mkdir()
        for candidate in (replace(self.selection.candidate, product_data=different),
                          replace(self.selection.candidate, server_python=None)):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    self.assess(replace(self.selection, candidate=candidate))

    def test_linked_data_spelling_keeps_both_roles(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.data, target_is_directory=True)
        result = self.assess(replace(self.selection, candidate=replace(
            self.selection.candidate, product_data=alias)))
        roots = {row['role']: row for row in result['sources']['capture']['roots']}
        self.assertEqual(roots['installed_persistent_python']['path'], str(self.data / 'server/driver_repo'))
        self.assertEqual(roots['candidate_persistent_python']['path'], str(alias / 'server/driver_repo'))
        self.assertEqual(result['selection']['launches']['candidate']['product_data'], str(alias))

    def test_selection_binding_has_no_arbitrary_configuration_or_environment(self):
        result = self.assess()
        self.assertEqual(set(result['selection']['launches']['installed']),
                         {'config_sha256', 'effective_launch_sha256', 'server_python',
                          'product_data', 'package_root', 'app_root'})
        with self.assertRaises(TypeError):
            ServerSelection(**{**result['selection']['launches']['installed'], 'password': 'unreported'})
        with self.assertRaises(ValueError):
            self.assess(replace(self.selection, pending_imports=None))
        changed = self.assess(replace(self.selection, candidate=replace(
            self.selection.candidate, config_sha256='7' * 64)))
        self.assertNotEqual(changed['selection_sha256'], result['selection_sha256'])


if __name__ == '__main__':
    unittest.main()
