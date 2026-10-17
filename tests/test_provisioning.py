"""Per-unit setup must preserve identity and refuse incomplete private state."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openavc_oem.provisioning import provision_unit, rotate_management_token


@unittest.skipUnless(os.name == 'posix', 'Unit files use the product Linux permission model')
class Provisioning(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_new_units_are_distinct_and_repeated_startup_preserves_private_bytes(self):
        first = provision_unit(self.root / 'one')
        second = provision_unit(self.root / 'two')
        token = first.management_token_file.read_bytes()
        self.assertNotEqual(first.identity, second.identity)
        self.assertNotEqual(token, second.management_token_file.read_bytes())
        self.assertTrue(first.created)
        again = provision_unit(self.root / 'one')
        self.assertFalse(again.created)
        self.assertEqual(again.identity, first.identity)
        self.assertEqual(again.management_token_file.read_bytes(), token)
        self.assertNotIn(token.decode(), repr(again))
        self.assertEqual((self.root / 'one').stat().st_mode & 0o777, 0o700)
        self.assertEqual(first.management_token_file.stat().st_mode & 0o777, 0o600)

    def test_factory_identity_must_match_and_token_rotation_preserves_identity(self):
        directory = self.root / 'unit'
        first = provision_unit(directory, identity='example:model:serial-123')
        record = (directory / 'unit.json').read_bytes()
        secret = first.management_token_file.read_bytes()
        with self.assertRaisesRegex(ValueError, 'differs'):
            provision_unit(directory, identity='replacement-unit')
        self.assertEqual(first.management_token_file.read_bytes(), secret)
        changed = rotate_management_token(directory)
        self.assertEqual(changed.identity, first.identity)
        self.assertNotEqual(changed.management_token_file.read_bytes(), secret)
        self.assertEqual((directory / 'unit.json').read_bytes(), record)

    def test_interrupted_creation_cannot_mint_a_replacement_identity(self):
        directory = self.root / 'interrupted'
        with patch('openavc_oem.provisioning._write_token', side_effect=OSError('storage unavailable')):
            with self.assertRaises(OSError):
                provision_unit(directory, identity='original-unit')
        self.assertTrue((directory / 'provisioning.pending').exists())
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            provision_unit(directory, identity='replacement-unit')
        self.assertFalse((directory / 'unit.json').exists())

    def test_missing_or_public_files_are_not_repaired_implicitly(self):
        for mode in ['missing', 'permissions', 'symlink', 'identity']:
            with self.subTest(mode=mode):
                directory = self.root / mode
                unit = provision_unit(directory)
                if mode == 'missing':
                    unit.management_token_file.unlink()
                elif mode == 'permissions':
                    unit.management_token_file.chmod(0o644)
                elif mode == 'symlink':
                    original = unit.management_token_file.read_bytes()
                    outside = self.root / 'outside'
                    outside.write_bytes(original)
                    outside.chmod(0o600)
                    unit.management_token_file.unlink()
                    unit.management_token_file.symlink_to(outside)
                else:
                    (directory / 'unit.json').write_text(json.dumps({'schema': True, 'identity': unit.identity}))
                with self.assertRaisesRegex(ValueError, 'missing or damaged'):
                    provision_unit(directory)

    def test_directory_links_are_refused_without_touching_the_target(self):
        target = self.root / 'target'
        unit = provision_unit(target)
        original = unit.management_token_file.read_bytes()
        link = self.root / 'link'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'private directory'):
            provision_unit(link)
        self.assertEqual(unit.management_token_file.read_bytes(), original)
