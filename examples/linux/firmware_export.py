"""Export installed firmware examples without recompiling target binaries."""
import hashlib
import json
from pathlib import Path
import platform
import shutil

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def export_examples(target, generated, destination, builder, build_target=None):
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
        shutil.copy2(generated / name, binaries / 'generated' / name)
    shutil.copy2(build_target or generated / 'build-target.json', binaries)
    for name in ['try-example.py', 'example_build.py', 'inspection.py', 'product_checks.py', 'check-product.py',
                 'provision-unit.py', 'collect-diagnostics.py', 'diagnostic_report.py']:
        shutil.copy2(ROOT / name, kit / name)
    (kit / 'sample').mkdir()
    shutil.copy2(ROOT / 'sample/checks.json', kit / 'sample/checks.json')
    # Exercise the installed Python package, without a source package in /kit
    # that could shadow it and make a missing firmware dependency pass.
    (kit / 'examples/linux').mkdir(parents=True)
    for name in ['check.py', 'check-provisioning.py', 'stage.py', 'hardware.service.in', 'management.service.in']:
        shutil.copy2(ROOT / 'examples/linux' / name, kit / 'examples/linux' / name)
    (kit / 'examples/linux/buildroot').mkdir()
    shutil.copy2(ROOT / 'examples/linux/buildroot/check-installed.py', kit / 'examples/linux/buildroot/check-installed.py')
    shutil.copy2(ROOT / 'LICENSE', kit)
    shutil.copytree(ROOT / 'licenses', kit / 'licenses')
    # The firmware walkthrough consumes the same installed binaries, through
    # its standard build/ path. No compilation takes place on the target.
    (kit / 'build').symlink_to('built', target_is_directory=True)
    (kit / 'sdk/contract').mkdir(parents=True)
    shutil.copy2(ROOT / 'sdk/contract/origin.json', kit / 'sdk/contract/origin.json')
    if digest(target / 'usr/share/openavc/contract/origin.json') != digest(kit / 'sdk/contract/origin.json'):
        raise ValueError('Run the exporter from the SDK checkout used by this firmware build.')
    inputs = ['generated/' + name for name in ['model.json', 'commands.h', 'commands.c',
                                              'model.provenance.json', 'provenance.json']]
    for language, form in [('c', 'service'), ('cpp', 'service'), ('c', 'direct')]:
        files = ['example-' + language, 'build-target.json', *inputs]
        if form == 'service':
            files.append('openavc-device-service')
        record = {'format': 1, 'status': 'built', 'language': language, 'form': form,
                  'builder': builder, 'validation': 'compilation_and_installation_only',
                  'build_host': {'system': platform.system(), 'machine': platform.machine()},
                  'contract_sha256': digest(kit / 'sdk/contract/origin.json'),
                  'files': {name: {'sha256': digest(binaries / name), 'bytes': (binaries / name).stat().st_size}
                            for name in files}}
        (binaries / f'{language}-{form}-build-result.json').write_text(json.dumps(record, indent=2) + '\n')
    return binaries


def write_manifest(destination, binaries):
    metadata = {'coverage': 'installed_examples_and_reference_rootfs',
                'target': json.loads((binaries / 'build-target.json').read_text()),
                'contract': json.loads((destination / 'kit/sdk/contract/origin.json').read_text()),
                'files': {str(path.relative_to(destination)): digest(path)
                          for path in sorted(destination.rglob('*')) if path.is_file()}}
    (destination / 'export.json').write_text(json.dumps(metadata, indent=2) + '\n')
