"""Check an installed candidate against its trusted offline server bundle.

Run in the candidate Python environment before stopping the current firmware.
This reads package files only; it neither starts OpenAVC nor accesses product data.
The bundle's authenticity and complete firmware acceptance belong to the host.
"""
import argparse
from email.parser import BytesParser
import hashlib
from importlib.util import find_spec
import json
from pathlib import Path, PurePosixPath
import re
import sys
from zipfile import BadZipFile, ZipFile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def package_root(name):
    spec = find_spec(name)
    locations = list(spec.submodule_search_locations or ()) if spec else []
    if len(locations) != 1:
        raise ValueError(f'The candidate must contain one installed {name} package.')
    return Path(locations[0]).resolve(strict=True)


def check_payload(wheel, package, root):
    expected = {}
    for member in wheel.infolist():
        if member.is_dir() or not member.filename.startswith(package + '/'):
            continue
        relative = member.filename[len(package) + 1:]
        path = PurePosixPath(relative)
        if (not relative or path.is_absolute() or '..' in path.parts
                or '\\' in relative or relative in expected):
            raise ValueError('Invalid or repeated package member in the server wheel.')
        expected[relative] = hashlib.sha256(wheel.read(member)).hexdigest()
    if '__init__.py' not in expected:
        raise ValueError(f'The server wheel does not contain {package}.')
    actual = {}
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if '__pycache__' in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError(f'The candidate package contains a symbolic link: {package}/{relative}')
        if path.is_file():
            actual[relative.as_posix()] = digest(path)
    differences = sorted(name for name in expected.keys() | actual.keys()
                         if expected.get(name) != actual.get(name))
    if differences:
        raise ValueError(f'The installed {package} files differ from the selected bundle: '
                         + ', '.join(differences[:5]))
    return len(expected)


def check(bundle, expected_core):
    if not re.fullmatch(r'[0-9a-f]{40}', expected_core):
        raise ValueError('Supply the complete core commit from your qualified firmware build.')
    record = json.loads((bundle / 'bundle.json').read_text(encoding='utf-8'))
    if record['core']['core_head'] != expected_core:
        raise ValueError('The server bundle does not match the selected core commit.')
    files = record['wheels']
    if not isinstance(files, dict) or not files:
        raise ValueError('The server bundle has no wheel inventory.')
    packages = {'openavc': 'openavc', 'openavc-oem': 'openavc_oem'}
    checked = {}
    for name, sha in files.items():
        if Path(name).name != name or '\\' in name or not name.endswith('.whl'):
            raise ValueError('Invalid wheel name in the server bundle.')
        path = bundle / 'wheels' / name
        if digest(path) != sha:
            raise ValueError(f'The server bundle wheel hash does not match: {name}')
        with ZipFile(path) as wheel:
            metadata = [entry for entry in wheel.namelist()
                        if re.fullmatch(r'[^/]+\.dist-info/METADATA', entry)]
            if len(metadata) != 1:
                raise ValueError(f'The wheel must contain exactly one metadata record: {name}')
            declared = BytesParser().parsebytes(wheel.read(metadata[0]))
            distribution = re.sub(r'[-_.]+', '-', declared.get('Name', '')).lower()
            if distribution not in packages:
                continue
            package = packages[distribution]
            if package in checked:
                raise ValueError(f'The server bundle repeats the {package} package.')
            root = package_root(package)
            checked[package] = {'origin': str(root), 'wheel': name, 'wheel_sha256': sha,
                                'checked_files': check_payload(wheel, package, root)}
    if set(checked) != set(packages.values()):
        raise ValueError('The server bundle must contain both OpenAVC and its manufacturer integration.')
    return {'status': 'passed', 'core_commit': expected_core, 'packages': checked}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path,
                        help='Extracted, trusted offline server bundle directory')
    parser.add_argument('--expect-core', required=True,
                        help='Complete core commit selected for this firmware build')
    args = parser.parse_args()
    try:
        result = check(args.bundle.resolve(strict=True), args.expect_core)
    except (OSError, ValueError, KeyError, TypeError, BadZipFile) as error:
        print(json.dumps({'status': 'refused', 'detail': str(error)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
