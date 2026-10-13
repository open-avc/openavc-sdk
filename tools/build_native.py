"""Build a Linux native-library archive in the fixed reference environment."""
import argparse
import contextlib
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def build(args):
    if shutil.which('docker') is None:
        raise ValueError('Install Docker and start a Linux container engine; see docs/building.md.')
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    docker = ['docker', *(['--context', args.docker_context] if args.docker_context else [])]
    image_file = output / 'build-image.txt'
    with (output / 'image-build.log').open('w') as log:
        subprocess.run(docker + ['build', '--iidfile', str(image_file), '-f',
                                str(ROOT / 'examples/linux/native/Dockerfile'), str(ROOT)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    image = image_file.read_text().strip()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Docker did not record a valid build image ID.')
    name = 'openavc-native-build-' + secrets.token_hex(6)
    try:
        subprocess.run(docker + ['create', '--name', name, '--network', 'none', '--user', '65534:65534',
                                image, '--out', '/tmp/output', '--build-image', image],
                       stdout=subprocess.DEVNULL, check=True)
        with (output / 'package-build.log').open('w') as log:
            attached = subprocess.run(docker + ['start', '--attach', name],
                                      stdout=log, stderr=subprocess.STDOUT, check=False)
        state = json.loads(subprocess.check_output(
            docker + ['inspect', '--format', '{{json .State}}', name], text=True))
        (output / 'container-state.json').write_text(json.dumps(state, indent=2) + '\n')
        # Preserve compiler output on a failed package build before removing its container.
        copied = subprocess.run(docker + ['cp', name + ':/tmp/output/.', str(output)], check=False)
        if attached.returncode or state['Status'] != 'exited' or state['ExitCode'] or copied.returncode:
            raise RuntimeError('Native package build failed. Read package-build.log and build.log.')
    finally:
        with contextlib.suppress(subprocess.SubprocessError):
            subprocess.run(docker + ['stop', '--time', '10', name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        with contextlib.suppress(subprocess.SubprocessError):
            subprocess.run(docker + ['rm', name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    print('Native library archive: ' + str(output))
    print('Test the extracted archive on the matching target; see docs/building.md.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--docker-context')
    try:
        build(parser.parse_args())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        parser.exit(1, 'Read image-build.log and package-build.log in the selected output directory.\n')
