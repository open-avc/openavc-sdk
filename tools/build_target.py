"""Cross-compile the C/C++ hardware examples for a reference Linux target."""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def build(args):
    if shutil.which('docker') is None:
        raise ValueError('Install Docker and start a Linux container engine; see docs/cross-building.md.')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    docker = ['docker', *(['--context', args.docker_context] if args.docker_context else [])]
    identity = out / 'image-id.txt'
    name = 'openavc-cross-' + secrets.token_hex(6)
    with (out / 'build.log').open('w') as log:
        subprocess.run(docker + ['build', '--iidfile', str(identity), '--build-arg', 'TARGET=' + args.target,
                                 '-f', str(ROOT / 'examples/linux/cross/Dockerfile'), str(ROOT)],
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    image = identity.read_text().strip()
    try:
        subprocess.run(docker + ['create', '--name', name, image], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(docker + ['cp', name + ':/output/.', str(out)], check=True)
    finally:
        with contextlib.suppress(subprocess.SubprocessError):
            subprocess.run(docker + ['rm', name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (out / 'container.json').write_text(json.dumps({'image': image, 'target': args.target}, indent=2) + '\n')
    files = [path for path in sorted(out.rglob('*')) if path.is_file()]
    artifact = out / 'target.tar.gz'
    with tarfile.open(artifact, 'w:gz') as archive:
        for path in files:
            archive.add(path, arcname='target/' + str(path.relative_to(out)))
    (out / 'target.tar.gz.sha256').write_text(hashlib.sha256(artifact.read_bytes()).hexdigest() + '  target.tar.gz\n')
    print('Cross-build complete: ' + str(out))
    print('No target binary was run. Continue with docs/cross-building.md on the matching target.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=['arm64', 'x86_64'], required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--docker-context')
    try:
        build(parser.parse_args())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\nRead build.log in the selected output directory.\n')
