"""Stage a local Linux firmware example without installing or starting services."""
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def stage(args):
    if sys.platform != 'linux':
        raise ValueError('Run this firmware example on Linux.')
    destination = args.out.resolve()
    runtime = args.runtime_dir.resolve() if args.runtime_dir else destination / 'run'
    if len(os.fsencode(runtime / 'hardware.sock')) >= 108:
        raise ValueError('The Linux socket path is too long. Select a short --runtime-dir, such as /run/my-product.')
    if runtime.exists():
        raise ValueError('Select a new runtime directory; existing runtime files are not replaced.')
    # Preserve a virtual environment's executable path; resolving its symlink
    # selects the base interpreter and loses the environment's installed packages.
    python = Path(os.path.abspath(args.python))
    args.python = python
    account = pwd.getpwnam(args.service_user) if args.service_user else pwd.getpwuid(os.getuid())
    if account.pw_uid == 0:
        raise ValueError('Select an unprivileged account with --service-user; this example does not run as root.')
    if os.getuid() not in (0, account.pw_uid):
        raise ValueError('Stage as the service account, or as root when assigning another service account.')
    identity = args.identity or 'product-' + secrets.token_hex(16)
    for value in (str(destination), str(runtime), str(python), identity, account.pw_name, grp.getgrgid(account.pw_gid).gr_name):
        if not re.fullmatch(r'[A-Za-z0-9_./-]+', value):
            raise ValueError('For these service templates, use paths and names containing letters, numbers, underscore, dot, slash or hyphen.')
    inputs = {'hardware': ROOT / ('build/example-' + args.language),
              'openavc-device-service': ROOT / 'build/openavc-device-service',
              'model.json': ROOT / 'build/generated/model.json'}
    for path in [*inputs.values(), python]:
        if not path.is_file():
            raise ValueError('Build the selected service example first and select an installed Python environment.')
    for port in (args.equipment_port, args.management_port, args.server_port):
        if not 1024 <= port <= 65535:
            raise ValueError('Choose unprivileged ports from 1024 to 65535.')
    if len({args.equipment_port, args.management_port, args.server_port}) != 3:
        raise ValueError('Choose different equipment, management and server ports.')
    destination.mkdir(parents=True, exist_ok=False, mode=0o755)
    for name in ('program', 'private', 'data', 'systemd'):
        (destination / name).mkdir(mode=0o755 if name in ('program', 'systemd') else 0o700)
    runtime.mkdir(mode=0o700, parents=True)
    (runtime / 'tmp').mkdir(mode=0o700)
    for name, path in inputs.items():
        shutil.copy2(path, destination / 'program' / name)
    shutil.copytree(ROOT / 'licenses', destination / 'program/licenses')
    shutil.copy2(ROOT / 'LICENSE', destination / 'program/licenses/openavc-sdk-LICENSE')
    if (ROOT / 'sdk/lib').is_dir():
        shutil.copytree(ROOT / 'sdk/lib', destination / 'program/lib')
    else:
        (destination / 'program/lib').mkdir()
    private = destination / 'private'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '7',
                    '-keyout', str(private / 'key.pem'), '-out', str(private / 'ca.pem'),
                    '-subj', '/CN=OpenAVC firmware example', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    (private / 'server.pem').write_bytes((private / 'ca.pem').read_bytes() + (private / 'key.pem').read_bytes())
    (private / 'key.pem').unlink()  # The private key is retained in server.pem.
    (private / 'management.token').write_text(secrets.token_hex(32))
    credentials = {'username': 'engineer', 'password': secrets.token_urlsafe(24)}
    (private / 'product-credentials.json').write_text(json.dumps(credentials, indent=2) + '\n')
    configuration = {'identity': identity, 'data_dir': str(destination / 'data'),
                     'model': str(destination / 'program/model.json'),
                     'device_service': str(destination / 'program/openavc-device-service'),
                     'bridge_path': str(runtime / 'hardware.sock'),
                     'certificate': str(private / 'server.pem'), 'ca_file': str(private / 'ca.pem'),
                     'management_token_file': str(private / 'management.token'),
                     'equipment_bind': '127.0.0.1', 'equipment_port': args.equipment_port,
                     'management_port': args.management_port, 'login': credentials}
    if args.with_server:
        configuration.update(server_python=str(python), firmware_version='example', server_environment={
            'OPENAVC_PORT': str(args.server_port), 'OPENAVC_BIND': '127.0.0.1', 'OPENAVC_CLOUD_ENABLED': 'false',
            'OPENAVC_MDNS_ADVERTISE': 'false', 'OPENAVC_PORT80_REDIRECT': 'false'})
    (private / 'openavc.json').write_text(json.dumps(configuration, indent=2) + '\n')
    replacements = {'ROOT': str(destination), 'PYTHON': str(python), 'IDENTITY': identity,
                    'RUNTIME': str(runtime),
                    'USER': account.pw_name, 'GROUP': grp.getgrgid(account.pw_gid).gr_name}
    for name in ('hardware', 'management'):
        unit = (ROOT / 'examples/linux' / (name + '.service.in')).read_text()
        for key, value in replacements.items():
            unit = unit.replace('@' + key + '@', value)
        (destination / 'systemd' / ('openavc-product-' + name + '.service')).write_text(unit)
    (destination / 'systemd/openavc-product.conf').write_text(
        f'd {runtime} 0700 {account.pw_name} {grp.getgrgid(account.pw_gid).gr_name} -\n'
        f'd {runtime}/tmp 0700 {account.pw_name} {grp.getgrgid(account.pw_gid).gr_name} -\n')
    for directory in (destination / 'private', destination / 'data', runtime):
        for path in [directory, *directory.rglob('*')]:
            if os.getuid() == 0:
                os.chown(path, account.pw_uid, account.pw_gid)
            path.chmod(0o700 if path.is_dir() else 0o600)
    for path in sorted((destination / 'program').rglob('*'), reverse=True):
        path.chmod(0o555 if path.is_dir() or path.name in ('hardware', 'openavc-device-service') else 0o444)
    (destination / 'program').chmod(0o555)
    metadata = {'format': 1, 'identity': identity, 'service_user': account.pw_name,
                'runtime_directory': str(runtime),
                'language': args.language,
                'python': str(python), 'with_server': args.with_server,
                'contract': json.loads((ROOT / 'sdk/contract/origin.json').read_text()),
                'program_sha256': {str(path.relative_to(destination)): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in (destination / 'program').rglob('*') if path.is_file()}}
    (destination / 'staging.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--out', type=Path, required=True, help='New absolute staging directory')
    result.add_argument('--service-user', help='Existing unprivileged account; defaults to current account')
    result.add_argument('--runtime-dir', type=Path, help='New short runtime directory; staging defaults to OUT/run, checks use temporary storage')
    result.add_argument('--language', choices=['c', 'cpp'], default='c', help='Built hardware example to stage')
    result.add_argument('--identity', help='Stable unique identity for this unit; generated if omitted')
    result.add_argument('--python', type=Path, default=Path(sys.executable), help='Installed firmware Python environment')
    result.add_argument('--with-server', action='store_true', help='Include the installed OpenAVC server in this example')
    result.add_argument('--equipment-port', type=int, default=19443)
    result.add_argument('--management-port', type=int, default=19081)
    result.add_argument('--server-port', type=int, default=19080)
    return result


if __name__ == '__main__':
    command = parser()
    args = command.parse_args()
    try:
        stage(args)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        command.exit(1, str(error) + '\n')
    print('Firmware example staged: ' + str(args.out.resolve()))
    print('No services were installed or started. Follow docs/firmware.md to run and inspect it.')
