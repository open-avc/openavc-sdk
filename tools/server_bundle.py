"""Produce wheels and provenance inside the selected build image."""

import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from wheel_inventory import digest, install_requirements, inventory, publisher_sboms


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def build(output):
    sdk = Path('/opt/sdk')
    core = Path('/opt/openavc')
    output.mkdir()
    shutil.copytree('/opt/server-wheels', output / 'wheels')
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-index', '--no-deps',
                    '--no-build-isolation', '--wheel-dir', str(output / 'wheels'),
                    str(core), str(sdk)], check=True)
    wheels = sorted((output / 'wheels').glob('*.whl'))
    if not any(path.name.startswith('openavc-') for path in wheels) or not any(path.name.startswith('openavc_oem-') for path in wheels):
        raise RuntimeError('The wheel bundle is incomplete. Read wheel-build.log.')
    runtime = {'system': platform.system(), 'machine': platform.machine(),
               'python': platform.python_version(), 'libc': platform.libc_ver()}
    write_json(output / 'runtime.json', runtime)
    bom = inventory(wheels)
    write_json(output / 'python-packages.cdx.json', bom)
    supplied = publisher_sboms(wheels, output / 'publisher-sboms')
    write_json(output / 'publisher-sboms/index.json', supplied)
    (output / 'requirements.txt').write_text(install_requirements(bom), encoding='utf-8')
    inputs = output / 'build-inputs'
    inputs.mkdir()
    for name in ['requirements-server.txt', 'requirements-build.txt']:
        shutil.copy2(sdk / 'tools' / name, inputs / name)
    shutil.copy2(sdk / 'examples/system/Dockerfile', inputs / 'Dockerfile')
    shutil.copytree('/opt/system-inputs', inputs / 'system-inputs')
    for interface in ['programmer', 'simulator']:
        shutil.copy2(core / f'openavc/web/{interface}/package-lock.json',
                     inputs / f'{interface}-package-lock.json')
        shutil.copytree(Path('/opt/browser-builds') / interface, inputs / 'browser-builds' / interface)
    (inputs / 'system-packages.txt').write_text(subprocess.check_output(
        ['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\n'],
        text=True), encoding='utf-8')
    sources = {}
    for name in ['openavc_oem', 'examples/system', 'sdk/contract', 'pyproject.toml',
                 'tools/build_server.py', 'tools/server_bundle.py', 'tools/wheel_inventory.py',
                 'tools/build_browser.mjs', 'tools/browser_inventory.mjs',
                 'tools/requirements-server.txt', 'tools/requirements-build.txt', 'tools/debian.sources']:
        source = sdk / name
        for path in sorted(source.rglob('*')) if source.is_dir() else [source]:
            if path.is_file() and '__pycache__' not in path.parts:
                sources[path.relative_to(sdk).as_posix()] = digest(path)
    metadata = {'core': json.loads((sdk / 'sdk/contract/origin.json').read_text(encoding='utf-8')),
                'runtime': runtime,
                'wheels': {path.name: digest(path) for path in wheels},
                'publisher_sboms': {path.name: digest(path) for path in sorted((output / 'publisher-sboms').iterdir())},
                'build_inputs': {path.relative_to(inputs).as_posix(): digest(path)
                                 for path in sorted(inputs.rglob('*')) if path.is_file()},
                'sdk_sources': sources,
                'core_build_inputs': {name: digest(core / name) for name in [
                    'pyproject.toml', 'openavc/web/programmer/package-lock.json',
                    'openavc/web/simulator/package-lock.json']},
                'inventories': {name: digest(output / name) for name in ['python-packages.cdx.json', 'requirements.txt']}}
    write_json(output / 'bundle.json', metadata)


if __name__ == '__main__':
    build(Path(sys.argv[1]))
