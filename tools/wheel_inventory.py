"""Inventory delivered Python wheels without importing or installing them."""

from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib
from pathlib import PurePosixPath
import re
from urllib.parse import quote
import uuid
import warnings
from zipfile import ZipFile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def wheel_component(path):
    with ZipFile(path) as archive:
        records = [entry for entry in archive.infolist()
                   if re.fullmatch(r'[^/]+\.dist-info/METADATA', entry.filename)]
        if len(records) != 1:
            raise ValueError(f'{path.name}: expected one wheel METADATA record.')
        metadata = BytesParser().parsebytes(archive.read(records[0]))
    values = {}
    for key in ['Metadata-Version', 'Name', 'Version']:
        entries = metadata.get_all(key, [])
        if len(entries) != 1 or not entries[0].strip():
            raise ValueError(f'{path.name}: missing or repeated {key}.')
        values[key] = entries[0].strip()
    version = values['Metadata-Version']
    if not re.fullmatch(r'\d+\.\d+', version) or int(version.split('.')[0]) > 2:
        raise ValueError(f'{path.name}: unsupported metadata version {version}.')
    if tuple(map(int, version.split('.'))) > (2, 6):
        warnings.warn(f'{path.name}: newer metadata version {version}; reading identity fields only.', stacklevel=2)
    name = values['Name']
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?', name):
        raise ValueError(f'{path.name}: invalid package name.')
    normalized = re.sub(r'[-_.]+', '-', name).lower()
    sha = digest(path)
    properties = [{'name': 'openavc:wheel:filename', 'value': path.name},
                  {'name': 'openavc:wheel:metadata-version', 'value': version}]
    # Preserve declarations as declarations; this is not a license review or
    # an evaluation of dependency markers for the firmware's environment.
    for key in ['License-Expression', 'License', 'Requires-Python', 'Requires-Dist']:
        properties.extend({'name': 'openavc:wheel:' + key.lower(), 'value': str(value)}
                          for value in metadata.get_all(key, []) if str(value).strip())
    return {'type': 'library', 'bom-ref': 'wheel-sha256:' + sha,
            'name': name, 'version': values['Version'],
            'purl': f'pkg:pypi/{normalized}@{quote(values["Version"], safe="")}',
            'hashes': [{'alg': 'SHA-256', 'content': sha}], 'properties': properties}


def inventory(paths):
    components = [wheel_component(path) for path in sorted(paths)]
    if not components:
        raise ValueError('The wheel directory contains no Python packages.')
    return {'$schema': 'http://cyclonedx.org/schema/bom-1.6.schema.json',
            'bomFormat': 'CycloneDX', 'specVersion': '1.6',
            'serialNumber': 'urn:uuid:' + str(uuid.uuid4()), 'version': 1,
            'metadata': {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'component': {'type': 'application', 'bom-ref': 'openavc-server-wheel-bundle',
                              'name': 'OpenAVC server Python wheel bundle'},
                'properties': [{'name': 'openavc:inventory:coverage', 'value':
                    'Delivered Python wheel archives and their declared metadata only. '
                    'Excludes nested native/JavaScript components, operating system, '
                    'Python interpreter, build tools and other firmware components. '
                    'Dependency relationships and license declarations are not evaluated.'}]},
            'components': components,
            'compositions': [{'aggregate': 'incomplete', 'assemblies': ['openavc-server-wheel-bundle']}]}


def publisher_sboms(paths, output):
    """Copy opaque PEP 770 documents, retaining their source wheel and member.

    Content-derived filenames avoid interpreting archive member paths as local
    paths. The documents remain publisher declarations, not verified coverage.
    """
    output.mkdir()
    records = {}
    for path in sorted(paths):
        documents = []
        with ZipFile(path) as archive:
            metadata = [entry.filename for entry in archive.infolist()
                        if re.fullmatch(r'[^/]+\.dist-info/METADATA', entry.filename)]
            if len(metadata) != 1:
                raise ValueError(f'{path.name}: expected one wheel METADATA record.')
            prefix = metadata[0].removesuffix('METADATA') + 'sboms/'
            seen = set()
            for entry in archive.infolist():
                if not entry.filename.startswith(prefix) or entry.is_dir():
                    continue
                if entry.filename in seen:
                    raise ValueError(f'{path.name}: repeated publisher SBOM member.')
                seen.add(entry.filename)
                content = archive.read(entry)
                sha = hashlib.sha256(content).hexdigest()
                suffix = PurePosixPath(entry.filename).suffix
                if not re.fullmatch(r'\.[A-Za-z0-9]{1,10}', suffix):
                    suffix = '.sbom'
                name = sha + suffix
                destination = output / name
                if not destination.exists():
                    destination.write_bytes(content)
                documents.append({'member': entry.filename, 'file': name, 'sha256': sha})
        records[path.name] = {'sha256': digest(path), 'documents': documents}
    return {'coverage': 'Publisher-supplied PEP 770 documents copied unchanged from delivered wheels. '
                         'An empty document list means none was supplied in that location. '
                         'Content, completeness and publisher signatures are not verified.',
            'wheels': records}


def install_requirements(bom):
    """Pin the delivered wheel set for pip's existing hash-checking mode."""
    packages = {}
    for component in bom['components']:
        name = re.sub(r'[-_.]+', '-', component['name']).lower()
        version = component['version']
        if not re.fullmatch(r'[A-Za-z0-9.!+_-]+', version):
            raise ValueError(f'{name}: invalid version for an installation requirement.')
        if name in packages and packages[name][0] != version:
            raise ValueError(f'{name}: multiple package versions in one server bundle.')
        packages.setdefault(name, (version, []))[1].append(component['hashes'][0]['content'])
    return '# Exact delivered wheels; use with --no-index and --require-hashes.\n' + ''.join(
        f'{name}=={version} ' + ' '.join('--hash=sha256:' + sha for sha in sorted(hashes)) + '\n'
        for name, (version, hashes) in sorted(packages.items()))
