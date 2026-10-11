"""Build a native C-facing SDK package with its source and contract provenance."""

import argparse
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root):
    return {str(path.relative_to(root)): digest(path) for path in sorted(root.rglob('*'))
            if path.is_file() and '__pycache__' not in path.parts}


def package(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    build = out / 'runtime-build'
    destination = out / 'openavc-sdk'
    system = platform.system()
    if system not in {'Linux', 'Darwin'}:
        raise ValueError('Build supplied runtimes on Linux or macOS.')
    configure = ['cmake', '-S', str(ROOT), '-B', str(build), '-DCMAKE_BUILD_TYPE=Release',
                 '-DOPENAVC_BUILD_EXAMPLES=OFF', '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON']
    if args.openssl:
        configure.append('-DOPENSSL_ROOT_DIR=' + str(args.openssl.resolve()))
    if system == 'Linux':
        configure += ['-DCMAKE_C_COMPILER=clang', '-DCMAKE_CXX_COMPILER=clang++',
                      '-DCMAKE_CXX_FLAGS=-stdlib=libc++', '-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON',
                      '-DCMAKE_INSTALL_RPATH=$ORIGIN/../lib']
    with (out / 'build.log').open('w') as log:
        subprocess.run(configure, check=True, stdout=log, stderr=subprocess.STDOUT)
        subprocess.run(['cmake', '--build', str(build), '--parallel', '2'],
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    for name in ['sample', 'licenses', 'docs', 'openavc_oem', 'examples']:
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, destination / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (destination / 'sample/example.cpp').unlink()
    for name in ['include', 'tools', 'contract']:
        shutil.copytree(ROOT / 'sdk' / name, destination / 'sdk' / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (destination / 'sdk/src').mkdir()
    shutil.copy2(ROOT / 'sdk/src/open_adapter.c', destination / 'sdk/src/open_adapter.c')
    for name in ['product.json', 'try-example.py', 'try-system.py', 'doctor.py', 'inspection.py',
                 'pyproject.toml', '.dockerignore', 'LICENSE']:
        shutil.copy2(ROOT / name, destination / name)
    shutil.copy2(ROOT / 'tools/supplied-README.md', destination / 'README.md')
    shutil.copy2(ROOT / 'tools/supplied-CMakeLists.txt', destination / 'CMakeLists.txt')
    (destination / 'tools').mkdir()
    shutil.copy2(ROOT / 'tools/build_server.py', destination / 'tools/build_server.py')
    lib = destination / 'sdk/lib'
    lib.mkdir()
    shutil.copy2(build / 'libopenavc_runtime.a', lib / 'libruntime.a')
    shutil.copy2(build / 'libhttp.a', lib / 'libhttp.a')
    origins = {}
    if system == 'Linux':
        triplet = {'aarch64': 'aarch64-linux-gnu', 'x86_64': 'x86_64-linux-gnu'}[platform.machine()]
        (destination / 'sdk/bin').mkdir()
        shutil.copy2(build / 'openavc-device-service', destination / 'sdk/bin/openavc-device-service')
        for name in ['libc++.so.1', 'libc++abi.so.1', 'libunwind.so.1']:
            source = (Path('/usr/lib') / triplet / name).resolve(strict=True)
            shutil.copy2(source, lib / name)
            # Debian/Ubuntu package metadata supplies each redistributed license.
            owner = subprocess.check_output(['dpkg-query', '-S', str(source)], text=True).split(': ', 1)[0].split(':')[0]
            shutil.copy2(Path('/usr/share/doc') / owner / 'copyright', destination / 'licenses' / (owner + '.copyright'))
            origins[name] = {'package': owner, 'sha256': digest(source)}
        shutil.copy2('/usr/share/common-licenses/Apache-2.0', destination / 'licenses/Apache-2.0.txt')
    files = {'sdk/' + name: sha for name, sha in inventory(destination / 'sdk').items()}
    (destination / 'sdk/sha256.json').write_text(json.dumps(files, indent=2) + '\n')
    sources = {}
    for name in ['sdk', 'sample', 'CMakeLists.txt', 'product.json', 'tools']:
        source = ROOT / name
        sources.update({name + '/' + key: value for key, value in inventory(source).items()}
                       if source.is_dir() else {name: digest(source)})
    metadata = {'delivery': 'supplied-c-runtime',
                'runtime_host': {'system': system, 'machine': platform.machine()},
                'libc': list(platform.libc_ver()), 'runtime_origins': origins,
                'contract': json.loads((ROOT / 'sdk/contract/origin.json').read_text()),
                'source_files': sources, 'files': inventory(destination)}
    (destination / 'bundle.json').write_text(json.dumps(metadata, indent=2) + '\n')
    artifact = out / ('openavc-sdk-' + system.lower() + '-' + platform.machine() + '.tar.gz')
    with tarfile.open(artifact, 'w:gz') as archive:
        archive.add(destination, arcname='openavc-sdk')
    artifact.with_suffix(artifact.suffix + '.sha256').write_text(digest(artifact) + '  ' + artifact.name + '\n')
    print(artifact)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='New output directory')
    parser.add_argument('--openssl', type=Path)
    package(parser.parse_args())
