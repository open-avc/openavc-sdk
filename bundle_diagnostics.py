"""Check a selected offline bundle without installing it or exporting its files."""
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from product_checks import read_json
from tools.wheel_inventory import digest, wheel_component


def version(value):
    return value if type(value) is str and re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}(?:(?:a|b|rc|\.post|\.dev)[0-9]+)?', value) else None


def inspect_bundle(folder):
    folder = Path(folder).resolve(strict=True)
    metadata_path = folder / 'bundle.json'
    metadata = read_json(metadata_path, limit=16 * 1024 * 1024)
    if type(metadata) is not dict:
        raise ValueError('Invalid bundle metadata.')
    wheels = metadata.get('wheels')
    inventories = metadata.get('inventories')
    if (type(wheels) is not dict or not wheels or len(wheels) > 4096
            or type(inventories) is not dict
            or set(inventories) != {'python-packages.cdx.json', 'requirements.txt'}):
        raise ValueError('Use a complete offline server bundle.')
    if set(path.name for path in (folder / 'wheels').glob('*.whl')) != set(wheels):
        raise ValueError('The wheel set differs from the bundle record.')
    packages = {}
    for directory, entries in [('wheels', wheels), ('', inventories)]:
        for name, expected in entries.items():
            if (type(name) is not str or not re.fullmatch(r'[A-Za-z0-9_.+!-]+', name)
                    or type(expected) is not str or not re.fullmatch(r'[0-9a-f]{64}', expected)):
                raise ValueError('Invalid bundle file record.')
            path = folder / directory / name
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(folder):
                raise ValueError('Bundle files must remain inside the selected directory.')
            if not path.is_file() or digest(path) != expected:
                raise ValueError('A bundle file does not match its recorded checksum.')
            if directory:
                if not name.endswith('.whl'):
                    raise ValueError('Invalid wheel record.')
                try:
                    with ZipFile(path) as archive:
                        records = [entry for entry in archive.infolist()
                                   if re.fullmatch(r'[^/]+\.dist-info/METADATA', entry.filename)]
                        if len(records) != 1 or records[0].file_size > 1024 * 1024:
                            raise ValueError('Invalid wheel metadata.')
                    component = wheel_component(path)
                except BadZipFile as error:
                    raise ValueError('Invalid wheel archive.') from error
                normalized = re.sub(r'[-_.]+', '-', component['name']).lower()
                if normalized in {'openavc', 'openavc-oem'}:
                    if normalized in packages:
                        raise ValueError('Repeated OpenAVC package in bundle.')
                    packages[normalized] = version(component['version'])
    if set(packages) != {'openavc', 'openavc-oem'}:
        raise ValueError('The server or product-management wheel is missing.')
    runtime = metadata.get('runtime')
    core = metadata.get('core')
    if type(runtime) is not dict or type(core) is not dict:
        raise ValueError('Bundle build context is missing.')
    image = metadata.get('build_image_id')
    commit = core.get('core_head')
    return {'coverage': 'selected_offline_bundle',
            'bundle_metadata_sha256': digest(metadata_path),
            'core_commit': commit if type(commit) is str and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit) else None,
            'build_image_id': image if type(image) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', image) else None,
            'build_runtime': {'system': runtime.get('system') if runtime.get('system') in ('Linux',) else None,
                              'machine': runtime.get('machine') if runtime.get('machine') in ('aarch64', 'x86_64') else None,
                              'python': version(runtime.get('python'))},
            'package_versions': packages, 'checked_wheels': len(wheels),
            'recorded_file_checksums_match': True,
            'publisher_signature_verified': False, 'installed_runtime_verified': False}
