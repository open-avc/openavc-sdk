"""Check wheel identity and incomplete-inventory reporting with independent data."""

import hashlib
from pathlib import Path
import tempfile
import unittest
import warnings
from zipfile import ZipFile

from tools.wheel_inventory import install_requirements, inventory, publisher_sboms


class WheelInventory(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.wheel = Path(self.folder.name) / 'example_audio-1.2-py3-none-any.whl'

    def write_wheel(self, metadata, second=False):
        with ZipFile(self.wheel, 'w') as archive:
            archive.writestr('example_audio-1.2.dist-info/METADATA', metadata)
            archive.writestr('example_audio/__init__.py', 'raise RuntimeError("must not import")\n')
            if second:
                archive.writestr('another-1.dist-info/METADATA', metadata)

    def test_identity_comes_from_archive_not_filename_and_coverage_is_explicit(self):
        self.write_wheel('Metadata-Version: 2.4\nName: Example.Audio\nVersion: 3.4+local\n'
                         'License-Expression: MIT\nRequires-Dist: another>=2; python_version>="3.12"\n')
        bom = inventory([self.wheel])
        self.assertEqual(bom['bomFormat'], 'CycloneDX')
        self.assertEqual(bom['specVersion'], '1.6')
        self.assertEqual(bom['compositions'][0]['aggregate'], 'incomplete')
        self.assertNotIn('dependencies', bom)
        component, = bom['components']
        self.assertEqual(component['name'], 'Example.Audio')
        self.assertEqual(component['version'], '3.4+local')
        self.assertEqual(component['purl'], 'pkg:pypi/example-audio@3.4%2Blocal')
        expected_hash = hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        self.assertEqual(component['hashes'], [{'alg': 'SHA-256', 'content': expected_hash}])
        properties = {p['name']: p['value'] for p in component['properties']}
        self.assertEqual(properties['openavc:wheel:license-expression'], 'MIT')
        self.assertEqual(properties['openavc:wheel:filename'], self.wheel.name)
        self.assertIn('example-audio==3.4+local --hash=sha256:' + expected_hash + '\n', install_requirements(bom))

    def test_missing_repeated_and_unsupported_metadata_are_refused(self):
        for metadata in ['Metadata-Version: 2.4\nName: Example\n',
                         'Metadata-Version: 2.4\nName: Example\nName: Other\nVersion: 1\n',
                         'Metadata-Version: 3.0\nName: Example\nVersion: 1\n']:
            with self.subTest(metadata=metadata):
                self.write_wheel(metadata)
                with self.assertRaises(ValueError):
                    inventory([self.wheel])
        self.write_wheel('Metadata-Version: 2.4\nName: Example\nVersion: 1\n', second=True)
        with self.assertRaisesRegex(ValueError, 'expected one'):
            inventory([self.wheel])
        with self.assertRaisesRegex(ValueError, 'no Python packages'):
            inventory([])

    def test_newer_minor_metadata_warns_and_missing_license_stays_missing(self):
        self.write_wheel('Metadata-Version: 2.7\nName: Example\nVersion: 1\n')
        with self.assertWarns(UserWarning):
            bom = inventory([self.wheel])
        self.assertNotIn('licenses', bom['components'][0])
        self.assertFalse(any('license' in value['name'] for value in bom['components'][0]['properties']))

    def test_install_manifest_refuses_ambiguous_versions_and_injected_options(self):
        self.write_wheel('Metadata-Version: 2.4\nName: Example\nVersion: 1\n')
        bom = inventory([self.wheel])
        bom['components'].append({**bom['components'][0], 'version': '2'})
        with self.assertRaisesRegex(ValueError, 'multiple package versions'):
            install_requirements(bom)
        bom['components'] = [bom['components'][0]]
        bom['components'][0]['version'] = '1 --extra-index-url https://example.invalid'
        with self.assertRaisesRegex(ValueError, 'invalid version'):
            install_requirements(bom)

    def test_publisher_documents_keep_original_bytes_and_do_not_follow_member_paths(self):
        self.write_wheel('Metadata-Version: 2.4\nName: Example\nVersion: 1\n')
        # SBOM data is opaque: include an unfamiliar format and a hostile path.
        payload = b'Publisher document\r\n\xff\n'
        member = 'example_audio-1.2.dist-info/sboms/../../../outside.txt'
        with ZipFile(self.wheel, 'a') as archive:
            archive.writestr(member, payload)
            archive.writestr('example_audio/internal-sbom.json', '{}')
        output = Path(self.folder.name) / 'documents'
        result = publisher_sboms([self.wheel], output)
        wheel = result['wheels'][self.wheel.name]
        self.assertEqual(wheel['sha256'], hashlib.sha256(self.wheel.read_bytes()).hexdigest())
        document, = wheel['documents']
        sha = hashlib.sha256(payload).hexdigest()
        self.assertEqual(document, {'member': member, 'file': sha + '.txt', 'sha256': sha})
        self.assertEqual((output / document['file']).read_bytes(), payload)
        self.assertEqual(list(output.iterdir()), [output / (sha + '.txt')])
        self.assertFalse((Path(self.folder.name) / 'outside.txt').exists())

    def test_absent_documents_are_explicit_and_duplicate_members_are_refused(self):
        self.write_wheel('Metadata-Version: 2.4\nName: Example\nVersion: 1\n')
        result = publisher_sboms([self.wheel], Path(self.folder.name) / 'empty')
        self.assertEqual(result['wheels'][self.wheel.name]['documents'], [])
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with ZipFile(self.wheel, 'a') as archive:
                for payload in ['first', 'second']:
                    archive.writestr('example_audio-1.2.dist-info/sboms/component.json', payload)
        with self.assertRaisesRegex(ValueError, 'repeated publisher'):
            publisher_sboms([self.wheel], Path(self.folder.name) / 'repeated')


if __name__ == '__main__':
    unittest.main()
