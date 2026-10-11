"""Build an offline Python wheel bundle for the optional Linux OpenAVC server."""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def build(args):
    if shutil.which('docker') is None:
        raise ValueError('Install and start a Linux Docker engine before building the server bundle.')
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    docker = ['docker', *(['--context', args.docker_context] if args.docker_context else [])]
    image = 'openavc-sdk-firmware-build:local'
    with (output / 'build.log').open('w') as log:
        subprocess.run(docker + ['build', '--target', 'server-runtime', '-f', str(ROOT / 'examples/system/Dockerfile'), '-t', image, str(ROOT)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    name = 'openavc-server-build-' + secrets.token_hex(6)
    command = (
        "import json,pathlib,platform,subprocess,sys; "
        "out=pathlib.Path('/output'); out.mkdir(); "
        "subprocess.run([sys.executable,'-m','pip','wheel','--wheel-dir',str(out/'wheels'),'/opt/openavc','/opt/sdk'],check=True); "
        "(out/'runtime.json').write_text(json.dumps({'system':platform.system(),'machine':platform.machine(),"
        "'python':platform.python_version(),'libc':platform.libc_ver()},indent=2)+'\\n')"
    )
    try:
        subprocess.run(docker + ['create', '--name', name, image, '-c', command],
                       check=True, stdout=subprocess.DEVNULL)
        with (output / 'wheel-build.log').open('w') as log:
            subprocess.run(docker + ['start', '--attach', name], check=True, stdout=log, stderr=subprocess.STDOUT)
        subprocess.run(docker + ['cp', name + ':/output/.', str(output)], check=True)
    finally:
        with contextlib.suppress(subprocess.SubprocessError):
            subprocess.run(docker + ['stop', '--time', '10', name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with contextlib.suppress(subprocess.SubprocessError):
            subprocess.run(docker + ['rm', name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wheels = sorted((output / 'wheels').glob('*.whl'))
    if not any(path.name.startswith('openavc-') for path in wheels) or not any(path.name.startswith('openavc_oem-') for path in wheels):
        raise RuntimeError('The wheel bundle is incomplete. Read wheel-build.log.')
    metadata = {'core': json.loads((ROOT / 'sdk/contract/origin.json').read_text()),
        'runtime': json.loads((output / 'runtime.json').read_text()),
        'wheels': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in wheels},
        'sdk_sources': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for directory in ['openavc_oem', 'examples/system']
                        for path in sorted((ROOT / directory).rglob('*'))
                        if path.is_file() and '__pycache__' not in path.parts}}
    (output / 'bundle.json').write_text(json.dumps(metadata, indent=2) + '\n')
    (output / 'INSTALL.txt').write_text(
        'Optional OpenAVC server and product-management Python bundle\n\n'
        'Use the matching Linux CPU architecture and Python 3.12 environment.\n'
        'Install in the firmware staging environment, not a running product:\n\n'
        '  python3 -m pip install --no-index --find-links=wheels openavc openavc-oem\n\n'
        'The native hardware adapter, device service, OpenSSL and Python interpreter\n'
        'are separate firmware components. See docs/product-controls.md and\n'
        'docs/building.md in the SDK checkout. Qualify the complete firmware.\n')
    print('Server bundle: ' + str(output))
    print('Offline installation instructions: ' + str(output / 'INSTALL.txt'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--docker-context')
    try:
        build(parser.parse_args())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
