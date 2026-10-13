"""Build an offline Python wheel bundle for the optional Linux OpenAVC server."""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def archive_bundle(output):
    runtime = json.loads((output / 'runtime.json').read_text(encoding='utf-8'))
    if runtime['system'] != 'Linux' or runtime['machine'] not in {'aarch64', 'x86_64'}:
        raise ValueError('Server archives require a Linux ARM64 or x86-64 build.')
    artifact = output / ('openavc-server-linux-' + runtime['machine'] + '.tar.gz')
    with tarfile.open(artifact, 'x:gz') as archive:
        for name in ['INSTALL.txt', 'bundle.json', 'runtime.json', 'python-packages.cdx.json',
                     'requirements.txt', 'build-image.txt', 'build-inputs', 'publisher-sboms', 'wheels']:
            archive.add(output / name, arcname='openavc-server/' + name)
    with artifact.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    artifact.with_suffix('.gz.sha256').write_text(sha + '  ' + artifact.name + '\n', encoding='utf-8')
    return artifact


def build(args):
    if shutil.which('docker') is None:
        raise ValueError('Install and start a Linux Docker engine before building the server bundle.')
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    docker = ['docker', *(['--context', args.docker_context] if args.docker_context else [])]
    image_file = output / 'build-image.txt'
    with (output / 'build.log').open('w') as log:
        subprocess.run(docker + ['build', '--target', 'server-runtime', '--iidfile', str(image_file),
                                '-f', str(ROOT / 'examples/system/Dockerfile'), str(ROOT)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    image = image_file.read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Docker did not record a valid build image ID. Read build.log.')
    name = 'openavc-server-build-' + secrets.token_hex(6)
    try:
        subprocess.run(docker + ['create', '--name', name, '--network', 'none', image,
                                '/opt/sdk/tools/server_bundle.py', '/output'],
                       check=True, stdout=subprocess.DEVNULL)
        with (output / 'wheel-build.log').open('w') as log:
            subprocess.run(docker + ['start', '--attach', name], check=True, stdout=log, stderr=subprocess.STDOUT)
        exit_code = subprocess.check_output(docker + ['inspect', '--format', '{{.State.ExitCode}}', name], text=True).strip()
        if exit_code != '0':
            raise RuntimeError('The server bundle build failed. Read wheel-build.log.')
        subprocess.run(docker + ['cp', name + ':/output/.', str(output)], check=True)
    finally:
        with contextlib.suppress(subprocess.SubprocessError):
            subprocess.run(docker + ['stop', '--time', '10', name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with contextlib.suppress(subprocess.SubprocessError):
            subprocess.run(docker + ['rm', name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    metadata = json.loads((output / 'bundle.json').read_text(encoding='utf-8'))
    metadata['build_image_id'] = image
    (output / 'bundle.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    (output / 'INSTALL.txt').write_text(
        'Optional OpenAVC server and product-management Python bundle\n\n'
        'Use the matching Linux CPU architecture and Python 3.12 environment.\n'
        'Install in a fresh firmware staging environment, not a running product.\n'
        'For local evaluation on a matching Linux host, from this directory:\n\n'
        '  python3.12 -m venv ../server-python\n'
        '  . ../server-python/bin/activate\n\n'
        'On Ubuntu, install python3-venv before creating the environment.\n'
        'Then install the bundle into the selected environment:\n\n'
        '  python3 -m pip install --no-index --find-links=wheels --require-hashes -r requirements.txt\n\n'
        'Use a fresh environment. This verifies each installed wheel against the\n'
        'recorded version and hash; retain the trusted requirements file.\n\n'
        'The native hardware adapter, device service, OpenSSL and Python interpreter\n'
        'are separate firmware components. See docs/product-controls.md and\n'
        'docs/building.md in the SDK checkout. Qualify the complete firmware.\n\n'
        'Retain bundle.json and python-packages.cdx.json with this firmware build.\n'
        'The CycloneDX inventory describes delivered Python wheels, not the complete\n'
        'firmware or nested native/JavaScript dependencies. publisher-sboms/index.json\n'
        'lists the original component inventories supplied inside those wheels;\n'
        'an empty document list means that wheel supplied none in the standard location.\n'
        'Their content and completeness are not verified. Hashes are not signatures.\n', encoding='utf-8')
    print('Server bundle: ' + str(output))
    print('Offline installation instructions: ' + str(output / 'INSTALL.txt'))
    print('Archive: ' + str(archive_bundle(output)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--docker-context')
    try:
        build(parser.parse_args())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
