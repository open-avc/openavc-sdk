"""Export the installed Buildroot examples for execution on the matching CPU."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def export(output, destination):
    output = output.resolve(strict=True)
    destination = destination.resolve()
    variables = subprocess.check_output(
        ['make', '-s', '-C', str(output), 'printvars', 'VARS=OPENAVC_SDK_BUILDDIR'], text=True)
    build = Path(next(line.split('=', 1)[1] for line in variables.splitlines()
                      if line.startswith('OPENAVC_SDK_BUILDDIR='))).resolve(strict=True)
    if not build.is_relative_to(output):
        raise ValueError('Use the output directory of this Buildroot example build.')
    config = (output / '.config').read_text()
    if 'BR2_PACKAGE_OPENAVC_SDK_EXAMPLES=y\n' not in config:
        raise ValueError('Enable the invented amplifier examples in the reference configuration.')
    target = output / 'target'
    destination.mkdir(parents=True, exist_ok=False)
    kit = destination / 'kit'
    binaries = kit / 'built'
    binaries.mkdir(parents=True)
    for name in ['example-c', 'example-cpp']:
        shutil.copy2(target / 'usr/libexec/openavc-examples' / name, binaries / name)
    shutil.copy2(target / 'usr/bin/openavc-device-service', binaries)
    (binaries / 'generated').mkdir()
    for name in ['model.json', 'model.provenance.json', 'provenance.json']:
        shutil.copy2(target / 'usr/share/openavc' / name, binaries / 'generated' / name)
    for name in ['commands.h', 'commands.c']:
        shutil.copy2(build / 'generated' / name, binaries / 'generated' / name)
    shutil.copy2(build / 'build-target.json', binaries)
    for name in ['try-example.py', 'example_build.py', 'inspection.py', 'product_checks.py']:
        shutil.copy2(ROOT / name, kit / name)
    # Exercise the installed Python package, without a source package in /kit
    # that could shadow it and make a missing firmware dependency pass.
    if 'BR2_PACKAGE_PYTHON_OPENAVC_OEM=y\n' not in config:
        raise ValueError('Enable product management for these complete firmware checks.')
    (kit / 'examples/linux').mkdir(parents=True)
    for name in ['check.py', 'stage.py', 'hardware.service.in', 'management.service.in']:
        shutil.copy2(ROOT / 'examples/linux' / name, kit / 'examples/linux' / name)
    (kit / 'examples/linux/buildroot').mkdir()
    shutil.copy2(Path(__file__).with_name('check-installed.py'), kit / 'examples/linux/buildroot/check-installed.py')
    shutil.copy2(ROOT / 'LICENSE', kit)
    shutil.copytree(ROOT / 'licenses', kit / 'licenses')
    # The firmware walkthrough consumes the same installed binaries, through
    # its standard build/ path. No compilation takes place on the target.
    (kit / 'build').symlink_to('built', target_is_directory=True)
    (kit / 'sdk/contract').mkdir(parents=True)
    shutil.copy2(ROOT / 'sdk/contract/origin.json', kit / 'sdk/contract/origin.json')
    if digest(target / 'usr/share/openavc/contract/origin.json') != digest(kit / 'sdk/contract/origin.json'):
        raise ValueError('Run the exporter from the SDK checkout used by this Buildroot build.')
    inputs = ['generated/' + name for name in ['model.json', 'commands.h', 'commands.c',
                                              'model.provenance.json', 'provenance.json']]
    for language, form in [('c', 'service'), ('cpp', 'service'), ('c', 'direct')]:
        files = ['example-' + language, 'build-target.json', *inputs]
        if form == 'service':
            files.append('openavc-device-service')
        record = {'format': 1, 'status': 'built', 'language': language, 'form': form,
                  'builder': 'Buildroot', 'validation': 'compilation_and_installation_only',
                  'build_host': {'system': platform.system(), 'machine': platform.machine()},
                  'contract_sha256': digest(kit / 'sdk/contract/origin.json'),
                  'files': {name: {'sha256': digest(binaries / name), 'bytes': (binaries / name).stat().st_size}
                            for name in files}}
        (binaries / f'{language}-{form}-build-result.json').write_text(json.dumps(record, indent=2) + '\n')
    shutil.copy2(output / '.config', destination / 'buildroot.config')
    shutil.copy2(output / 'images/rootfs.tar', destination)
    packages = json.loads(subprocess.check_output(
        ['make', '-s', '-C', str(output), 'show-info'], text=True))
    (destination / 'buildroot-packages.json').write_text(json.dumps(packages, indent=2) + '\n')
    metadata = {'coverage': 'installed_examples_and_reference_rootfs',
                'target': json.loads((binaries / 'build-target.json').read_text()),
                'contract': json.loads((kit / 'sdk/contract/origin.json').read_text()),
                'files': {str(path.relative_to(destination)): digest(path)
                          for path in sorted(destination.rglob('*')) if path.is_file()}}
    (destination / 'export.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print('Buildroot example exported to ' + str(destination))
    print('No target binary was executed. Use the matching native Linux CPU to run the checks.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--buildroot-output', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    export(args.buildroot_output, args.out)
