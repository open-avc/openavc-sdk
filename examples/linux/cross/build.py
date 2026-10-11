"""Build reference Linux targets without executing target binaries."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
target = os.environ['OPENAVC_CROSS_TARGET']
processor, triple, elf_machine = {'arm64': ('aarch64', 'aarch64-linux-gnu', 183),
                                 'x86_64': ('x86_64', 'x86_64-linux-gnu', 62)}[target]
environment = {**os.environ, 'OPENAVC_TARGET_PROCESSOR': processor,
               'OPENAVC_TARGET_TRIPLE': triple, 'OPENAVC_TARGET_SYSROOT': '/opt/sysroot'}
output = Path('/output')
output.mkdir()
build = ROOT / 'build-cross'
for language, form in [('c', 'service'), ('cpp', 'service'), ('c', 'direct')]:
    subprocess.run([sys.executable, str(ROOT / 'try-example.py'), '--build-only',
                    '--language', language, '--form', form, '--build-dir', str(build),
                    '--toolchain', str(Path(__file__).with_name('toolchain.cmake'))],
                   env=environment, check=True)
    record = build / f'{language}-{form}-build-result.json'
    for name in json.loads(record.read_text())['files']:
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build / name, destination)
    shutil.copy2(record, output / record.name)
    shutil.copy2(build / f'{language}-{form}-build.log', output)

binary_reports = {}
for name in ['example-c', 'example-cpp', 'openavc-device-service']:
    path = output / name
    data = path.read_bytes()
    if data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != elf_machine:
        raise ValueError('Output is not a 64-bit binary for the requested CPU: ' + name)
    binary_reports[name] = subprocess.check_output([triple + '-readelf', '-h', '-l', '-d', str(path)], text=True)
    (output / (name + '-elf.txt')).write_text(binary_reports[name])
shutil.copy2('/opt/build-packages.txt', output)
shutil.copy2('/opt/target-packages.sha256', output)
metadata = {'format': 1, 'coverage': 'cross_compilation_only',
            'build_host': {'system': platform.system(), 'machine': platform.machine()},
            'target': {'system': 'Linux', 'machine': processor, 'reference': 'Debian 12'},
            'compiler': subprocess.check_output([triple + '-g++', '--version'], text=True),
            'contract': json.loads((ROOT / 'sdk/contract/origin.json').read_text()),
            'files': {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(output.rglob('*')) if p.is_file()}}
(output / 'cross-build.json').write_text(json.dumps(metadata, indent=2) + '\n')
