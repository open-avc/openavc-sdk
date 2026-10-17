"""Protected per-unit identity and local management access for Linux firmware.

Product accounts and TLS certificates belong to the manufacturer's existing
provisioning process. This module never changes them or starts a service.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

from .storage import save_json, sync_parent


@dataclass(frozen=True)
class Unit:
    identity: str
    management_token_file: Path
    created: bool = False


def _private(path, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_mode & 0o077:
        raise ValueError('Unit setup requires a private directory and regular private files.')


def _read(directory):
    _private(directory, directory=True)
    if (directory / 'provisioning.pending').exists():
        raise ValueError('Unit setup is incomplete. Finish factory provisioning before starting OpenAVC.')
    try:
        record_path = directory / 'unit.json'
        token_path = directory / 'management.token'
        _private(record_path)
        _private(token_path)
        record = json.loads(record_path.read_text(encoding='utf-8'))
        if (type(record) is not dict or type(record.get('schema')) is not int or record['schema'] != 1
                or not isinstance(record.get('identity'), str) or not record['identity'].strip()):
            raise ValueError('Invalid unit identity record.')
        if not re.fullmatch('[0-9a-f]{64}', token_path.read_text(encoding='ascii')):
            raise ValueError('Invalid unit management credential.')
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError('Unit setup is missing or damaged. Restore its protected configuration; do not generate another identity.') from error
    return Unit(record['identity'], token_path)


def _write_token(path):
    # Publish one complete secret. A reader observes either the old credential
    # or the new one; no token value enters a return value or exception message.
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='ascii', dir=path.parent,
                                         prefix='.management-', delete=False) as stream:
            pending = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(secrets.token_hex(32))
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
        sync_parent(path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def provision_unit(directory, *, identity=None):
    """Create once, or validate and reuse this unit's protected configuration.

    Call from the product's first-boot/factory process as the service account.
    Give each unit a separate directory. An optional factory identity must match
    on subsequent calls. Existing, incomplete configuration is never replaced.
    """
    if os.name != 'posix':
        raise ValueError('Provision unit files on the product Linux filesystem.')
    directory = Path(directory).absolute()
    if identity is not None and (not isinstance(identity, str) or not identity.strip()):
        raise ValueError('Use a nonempty factory identity, or omit it to generate one.')
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        unit = _read(directory)
        if identity is not None and identity != unit.identity:
            raise ValueError('The supplied factory identity differs from this unit. Keep its original identity.')
        return unit
    # The directory itself reserves initialization. A failed/overlapping call
    # finds incomplete state and refuses instead of minting another identity.
    sync_parent(directory)
    save_json(directory / 'provisioning.pending', {'schema': 1})
    _write_token(directory / 'management.token')
    chosen = identity if identity is not None else 'product-' + secrets.token_hex(16)
    save_json(directory / 'unit.json', {'schema': 1, 'identity': chosen})
    (directory / 'provisioning.pending').unlink()
    sync_parent(directory / 'unit.json')
    return Unit(chosen, directory / 'management.token', created=True)


def rotate_management_token(directory):
    """Replace only local management access; call with management stopped.

    Restart management and have the manufacturer's backend reread the token
    file. Product accounts, equipment TLS and Programmer access are unchanged.
    """
    directory = Path(directory).absolute()
    unit = _read(directory)
    _write_token(unit.management_token_file)
    return unit
