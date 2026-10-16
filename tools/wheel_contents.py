"""Retain wheel notices and identify native payloads without loading package code."""

from email.parser import BytesParser
import hashlib
from pathlib import PurePosixPath
import re
import struct
from zipfile import ZipFile

try:
    from .wheel_inventory import digest, wheel_component
except ImportError:
    from wheel_inventory import digest, wheel_component


def native_identity(content):
    """Read only the ELF identification/header, never execute a shared library."""
    if not content.startswith(b'\x7fELF'):
        return None
    if len(content) < 20 or content[4] not in (1, 2) or content[5] not in (1, 2):
        raise ValueError('Malformed ELF identification in a wheel member.')
    order = '<' if content[5] == 1 else '>'
    kind, machine = struct.unpack_from(order + 'HH', content, 16)
    return {'format': 'ELF', 'bits': 32 if content[4] == 1 else 64,
            'byte_order': 'little' if content[5] == 1 else 'big',
            'type': kind, 'machine': machine}


def _save(output, content):
    sha = hashlib.sha256(content).hexdigest()
    name = sha + '.txt'
    (output / name).write_bytes(content)
    return {'file': name, 'sha256': sha}


def collect(paths, output, supplements=()):
    """Copy notices as opaque bytes, with exact archive-member provenance.

    Filename/metadata recognition does not establish license completeness. Source
    supplements are explicit inputs, tied to the exact wheels they supplement.
    No archive member name is used as an output path.
    """
    output.mkdir()
    wheels = {}
    for path in sorted(paths):
        component = wheel_component(path)
        row = {'name': component['name'], 'version': component['version'],
               'sha256': digest(path), 'notices': [], 'native_members': []}
        with ZipFile(path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise ValueError(f'{path.name}: repeated wheel member.')
            metadata_name, = [name for name in names
                              if re.fullmatch(r'[^/]+\.dist-info/METADATA', name)]
            metadata = BytesParser().parsebytes(archive.read(metadata_name))
            info = metadata_name.removesuffix('METADATA')
            declared = set()
            for name in metadata.get_all('License-File', []):
                # PEP 639 places these paths under dist-info/licenses. Legacy
                # producers also wrote them directly under dist-info.
                candidates = [info + 'licenses/' + name, info + name]
                found = [candidate for candidate in candidates if candidate in names]
                if not found:
                    raise ValueError(f'{path.name}: declared license file {name!r} is missing.')
                declared.update(found)
            for entry in entries:
                if entry.is_dir():
                    continue
                name = entry.filename
                with archive.open(entry) as stream:
                    header = stream.read(20)
                native = native_identity(header)
                if native is not None:
                    content = archive.read(entry)
                    row['native_members'].append({
                        'member': name, 'sha256': hashlib.sha256(content).hexdigest(),
                        'size': len(content), **native})
                recognized = re.fullmatch(
                    r'(?:licen[cs]e|copying|notice|authors)(?:[._-].*)?',
                    PurePosixPath(name).name, re.IGNORECASE)
                if name in declared or recognized:
                    row['notices'].append({'member': name,
                        'selection': 'metadata' if name in declared else 'filename',
                        **_save(output, archive.read(entry))})
        wheels[path.name] = row
    for item in supplements:
        # An upstream wheel update must not inherit an older notice supplement.
        matching = [row for row in wheels.values() if row['sha256'] in item['wheel_sha256']]
        if not matching:
            raise ValueError('Notice supplement does not match any delivered wheel: ' + item['origin'])
        content = item['path'].read_bytes()
        if hashlib.sha256(content).hexdigest() != item['sha256']:
            raise ValueError('Notice supplement hash mismatch: ' + item['origin'])
        notice = {'origin': item['origin'], 'selection': 'source-supplement',
                  **_save(output, content)}
        if 'source_archive_sha256' in item:
            notice['source_archive_sha256'] = item['source_archive_sha256']
        for row in matching:
            row['notices'].append(dict(notice))
    return {'coverage':
        'Notice files selected from wheel metadata or filenames, plus explicitly identified source supplements. '
        'Native entries identify ELF archive members, not their statically linked components. '
        'Empty lists do not prove absence of notice obligations or nested dependencies. '
        'No wheel code was loaded; license completeness is not inferred.',
        'wheels': wheels}
