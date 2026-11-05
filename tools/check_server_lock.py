"""Check the selected server's requirements against the exact packaged inputs.

The source check is offline and runs before a core pin can be changed. With a
wheel directory, also verify archive hashes and the complete dependency closure,
including extras, for the named Linux/Python target. Package code is never loaded.
"""

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from zipfile import ZipFile

try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version
    from packaging.specifiers import SpecifierSet
    from packaging.tags import cpython_tags, compatible_tags
    from packaging.utils import parse_wheel_filename
except ImportError:
    raise SystemExit('Install the check tools: python -m pip install --require-hashes '
                     '-r tools/requirements-check.txt')

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ('aarch64', 'x86_64')


def environment(machine):
    return {'implementation_name': 'cpython', 'implementation_version': '3.12.0',
            'os_name': 'posix', 'platform_machine': machine, 'platform_release': '',
            'platform_system': 'Linux', 'platform_version': '', 'python_version': '3.12',
            'python_full_version': '3.12.0', 'platform_python_implementation': 'CPython',
            'sys_platform': 'linux', 'extra': ''}


def platforms(machine):
    # Debian bookworm uses glibc 2.36. Do not select newer Ubuntu-host wheels.
    minimum = 5 if machine == 'x86_64' else 17
    legacy = [f'manylinux2014_{machine}']
    if machine == 'x86_64':
        legacy += ['manylinux2010_x86_64', 'manylinux1_x86_64']
    return [f'manylinux_2_{minor}_{machine}' for minor in range(36, minimum - 1, -1)] + legacy


def read_lock(path):
    packages = {}
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        requirement = Requirement(parts[0])
        specs = list(requirement.specifier)
        hashes = {part.removeprefix('--hash=sha256:') for part in parts[1:]}
        if (requirement.url or requirement.marker or requirement.extras or len(specs) != 1
                or specs[0].operator != '==' or '*' in specs[0].version or not hashes
                or any(not re.fullmatch(r'--hash=sha256:[0-9a-f]{64}', p) for p in parts[1:])):
            raise ValueError(f'{path}:{number}: use one exact version and SHA-256 wheel hashes.')
        name = canonicalize_name(requirement.name)
        if name in packages:
            raise ValueError(f'{path}:{number}: repeated package {name}.')
        packages[name] = {'version': Version(specs[0].version), 'hashes': hashes}
    if not packages:
        raise ValueError('The server dependency lock is empty.')
    return packages


def require(requirement, packages, owner, env, extras=()):
    req = Requirement(requirement)
    if req.marker and not any(req.marker.evaluate({**env, 'extra': extra})
                              for extra in ('', *extras)):
        return None
    name = canonicalize_name(req.name)
    if req.url or name not in packages or packages[name]['version'] not in req.specifier:
        actual = str(packages[name]['version']) if name in packages else 'missing'
        raise ValueError(f'{owner} requires {req}; server lock has {name} {actual}. '
                         'Update tools/requirements-server.txt and its wheel notices together.')
    return name, req.extras


def check_sources(core, revision, root=ROOT):
    source = subprocess.check_output(['git', '-C', str(core), 'show', f'{revision}:pyproject.toml'])
    projects = [('OpenAVC', tomllib.loads(source.decode())['project']),
                ('SDK', tomllib.loads((root / 'pyproject.toml').read_text())['project'])]
    packages = read_lock(root / 'tools/requirements-server.txt')
    requirements = []
    for owner, project in projects:
        if '3.12.0' not in SpecifierSet(project.get('requires-python', '')):
            raise ValueError(f'{owner} no longer supports the packaged Python 3.12 environment.')
        for spec in project.get('dependencies', []):
            requirements.append((owner, spec))
            for machine in TARGETS:
                require(spec, packages, owner, environment(machine))
    source_targets = [target
                      for source in json.loads((root / 'tools/wheel-notice-sources.json').read_text())['sources']
                      for target in source['applies_to']]
    retained = root / 'licenses/wheels/index.json'
    if retained.is_file():
        source_targets += json.loads(retained.read_text())['notices']
    for target in source_targets:
        name = canonicalize_name(target['package'])
        pin = packages.get(name)
        if (pin is None or Version(target['version']) != pin['version']
                or not target['wheel_sha256']
                or not set(target['wheel_sha256']) <= pin['hashes']):
            raise ValueError(f'Wheel notices for {name} {target["version"]} do not match '
                             'tools/requirements-server.txt. Review and regenerate the notices.')
    return packages, requirements


def check_wheels(directory, packages, requirements, machine):
    env = environment(machine)
    supported = set(cpython_tags((3, 12), ['cp312'], platforms(machine)))
    supported.update(compatible_tags((3, 12), 'cp312', platforms(machine)))
    metadata = {}
    for path in sorted(directory.glob('*.whl')):
        if not parse_wheel_filename(path.name)[3] & supported:
            raise ValueError(f'{path.name}: wheel does not support Linux {machine}/Python 3.12.')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with ZipFile(path) as archive:
            names = [n for n in archive.namelist() if re.fullmatch(r'[^/]+\.dist-info/METADATA', n)]
            if len(names) != 1:
                raise ValueError(f'{path.name}: expected one wheel metadata record.')
            record = BytesParser().parsebytes(archive.read(names[0]))
        name = canonicalize_name(record['Name'])
        if (name not in packages or name in metadata or digest not in packages[name]['hashes']
                or Version(record['Version']) != packages[name]['version']):
            raise ValueError(f'{path.name}: wheel identity/hash differs from the lock or is repeated.')
        if env['python_full_version'] not in SpecifierSet(record.get('Requires-Python', '')):
            raise ValueError(f'{path.name}: wheel does not support Python 3.12.')
        metadata[name] = record
    if set(metadata) != set(packages):
        raise ValueError('Wheel directory is incomplete: ' + ', '.join(sorted(set(packages) - set(metadata))))
    # Every supplied wheel must be installable, including a locked package that
    # the roots no longer use. Propagate requested extras until the graph settles.
    active = {name: set() for name in packages}
    pending = set(packages)
    for owner, spec in requirements:
        selected = require(spec, packages, owner, env)
        if selected:
            name, extras = selected
            active[name].update(extras)
    while pending:
        name = pending.pop()
        for spec in metadata[name].get_all('Requires-Dist', []):
            selected = require(spec, packages, name, env, active[name])
            if selected:
                child, extras = selected
                if not extras <= active[child]:
                    active[child].update(extras)
                    pending.add(child)
    return len(metadata)


def download_wheels(directory, machine, root=ROOT):
    directory.mkdir(parents=True, exist_ok=True)
    options = [option for value in platforms(machine) for option in ('--platform', value)]
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
                    '--require-hashes', '--no-deps', '--python-version', '3.12',
                    '--implementation', 'cp', '--abi', 'cp312', '--abi', 'abi3', '--abi', 'none',
                    *options, '-r', str(root / 'tools/requirements-server.txt'), '-d', str(directory)],
                   check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--revision', help='Defaults to the recorded SDK core pin.')
    parser.add_argument('--wheels', type=Path)
    parser.add_argument('--machine', choices=TARGETS)
    parser.add_argument('--download', action='store_true', help='Download locked target wheels before checking them.')
    args = parser.parse_args()
    if bool(args.wheels) != bool(args.machine):
        parser.error('--wheels and --machine must be supplied together.')
    if args.download and not args.wheels:
        parser.error('--download requires --wheels and --machine.')
    revision = args.revision or json.loads((ROOT / 'sdk/contract/origin.json').read_text())['core_head']
    packages, requirements = check_sources(args.core, revision)
    if args.download:
        download_wheels(args.wheels, args.machine)
    count = check_wheels(args.wheels, packages, requirements, args.machine) if args.wheels else 0
    print(json.dumps({'core': revision, 'locked_packages': len(packages),
                      'verified_wheels': count, 'machine': args.machine}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Server dependency check failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
