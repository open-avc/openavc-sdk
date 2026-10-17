"""Build a native C-facing SDK package with its source and contract provenance."""

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import re
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


def package_origin(source):
    environment = {**os.environ, 'LC_ALL': 'C.UTF-8'}
    owners = subprocess.check_output(['dpkg-query', '-S', str(source)], text=True, env=environment).splitlines()
    owners = [line.split(': ', 1)[0] for line in owners if line.endswith(': ' + str(source))]
    if len(owners) != 1 or ', ' in owners[0]:
        raise ValueError('Cannot identify one installed package for ' + str(source))
    fields = ('Package', 'Version', 'Architecture', 'source:Package', 'source:Version')
    form = '\t'.join('${' + field + '}' for field in fields) + '\n'
    values = subprocess.check_output(['dpkg-query', '-W', '-f=' + form, owners[0]],
                                     text=True, env=environment).strip().split('\t')
    if len(values) != len(fields) or not all(values):
        raise ValueError('Installed package metadata is incomplete for ' + str(source))
    return dict(zip(('package', 'version', 'architecture', 'source_package', 'source_version'), values))


def native_build_record(build):
    cache = {}
    for line in (build / 'CMakeCache.txt').read_text().splitlines():
        if line and not line.startswith(('#', '//')) and '=' in line:
            field, value = line.split('=', 1)
            cache[field.split(':', 1)[0]] = value
    tools = {}
    for name, key in (('c_compiler', 'CMAKE_C_COMPILER'), ('cxx_compiler', 'CMAKE_CXX_COMPILER'),
                      ('cmake', 'CMAKE_COMMAND')):
        path = Path(cache[key]).resolve(strict=True)
        tools[name] = {'command': Path(cache[key]).name, 'file': path.name, 'sha256': digest(path),
                       'version_output': subprocess.check_output([str(path), '--version'], text=True).strip()}
    libraries = {}
    for key in ('OPENSSL_SSL_LIBRARY', 'OPENSSL_CRYPTO_LIBRARY'):
        path = Path(cache[key]).resolve(strict=True)
        libraries[key] = {'file': path.name, 'sha256': digest(path)}
    settings = {key: value for key, value in cache.items() if key in {
        'CMAKE_BUILD_TYPE', 'CMAKE_GENERATOR', 'CMAKE_C_FLAGS', 'CMAKE_C_FLAGS_RELEASE',
        'CMAKE_CXX_FLAGS', 'CMAKE_CXX_FLAGS_RELEASE', 'CMAKE_EXE_LINKER_FLAGS',
        'CMAKE_EXE_LINKER_FLAGS_RELEASE', 'CMAKE_OSX_ARCHITECTURES', 'CMAKE_OSX_DEPLOYMENT_TARGET'}}
    distribution = None
    if platform.system() == 'Linux':
        distribution = {key: value for key, value in platform.freedesktop_os_release().items()
                        if key in {'ID', 'VERSION_ID', 'VERSION_CODENAME', 'PRETTY_NAME'}}
    return {'format': 1, 'tools': tools, 'cmake_settings': settings, 'openssl_link_inputs': libraries,
            'distribution': distribution,
            'coverage': 'Native build tools, selected CMake settings and OpenSSL link inputs. '
                        'This is build context, not a complete dependency inventory or an ABI compatibility guarantee.'}


def package(args):
    if args.build_image and not re.fullmatch(r'sha256:[0-9a-f]{64}', args.build_image):
        raise ValueError('Use a complete Docker image ID for --build-image.')
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
    for name in ['product.json', 'try-example.py', 'example_build.py', 'try-system.py', 'doctor.py', 'inspection.py', 'product_checks.py', 'check-product.py',
                 'resource_profile.py', 'profile-product.py', 'profile_comparison.py', 'compare-profiles.py',
                 'diagnostic_report.py', 'bundle_diagnostics.py', 'runtime_diagnostics.py', 'collect-diagnostics.py',
                 'new-product.py', 'provision-unit.py',
                 'pyproject.toml', '.dockerignore', 'LICENSE']:
        shutil.copy2(ROOT / name, destination / name)
    shutil.copy2(ROOT / 'tools/supplied-README.md', destination / 'README.md')
    shutil.copy2(ROOT / 'tools/supplied-CMakeLists.txt', destination / 'CMakeLists.txt')
    (destination / 'tools').mkdir()
    for name in ['build_server.py', 'server_bundle.py', 'wheel_inventory.py',
                 'build_browser.mjs', 'browser_inventory.mjs',
                 'requirements-server.txt', 'requirements-build.txt', 'debian.sources']:
        shutil.copy2(ROOT / 'tools' / name, destination / 'tools' / name)
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
            origin = package_origin(source)
            owner = origin['package']
            shutil.copy2(Path('/usr/share/doc') / owner / 'copyright', destination / 'licenses' / (owner + '.copyright'))
            origins[name] = {**origin, 'sha256': digest(source), 'copyright': 'licenses/' + owner + '.copyright'}
        shutil.copy2('/usr/share/common-licenses/Apache-2.0', destination / 'licenses/Apache-2.0.txt')
    (destination / 'native-build.json').write_text(json.dumps(native_build_record(build), indent=2) + '\n')
    if args.build_image:
        shutil.copytree('/opt/native-build-inputs', destination / 'build-inputs')
    files = {'sdk/' + name: sha for name, sha in inventory(destination / 'sdk').items()}
    (destination / 'sdk/sha256.json').write_text(json.dumps(files, indent=2) + '\n')
    sources = {}
    for name in ['sdk', 'sample', 'cmake', 'CMakeLists.txt', 'product.json', 'tools']:
        source = ROOT / name
        sources.update({name + '/' + key: value for key, value in inventory(source).items()}
                       if source.is_dir() else {name: digest(source)})
    metadata = {'delivery': 'supplied-c-runtime',
                'runtime_host': {'system': system, 'machine': platform.machine()},
                'libc': list(platform.libc_ver()), 'runtime_origins': origins,
                'contract': json.loads((ROOT / 'sdk/contract/origin.json').read_text()),
                'source_files': sources, 'files': inventory(destination)}
    if args.build_image:
        metadata['build_image_id'] = args.build_image
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
    parser.add_argument('--build-image', help='Image identity supplied by tools/build_native.py')
    package(parser.parse_args())
