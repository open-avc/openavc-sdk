"""Run an invented product with the C hardware example and SDK controls."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys

from openavc_oem.manager import Manager

ROOT = Path(__file__).resolve().parents[2]
DATA = Path('/data')


async def main():
    DATA.mkdir(mode=0o700, exist_ok=True)
    DATA.chmod(0o700)
    identity = os.environ['EXAMPLE_IDENTITY']
    credentials = json.loads((DATA / 'login.json').read_text())
    address = socket.gethostbyname(socket.gethostname())
    if not (DATA / 'server.pem').exists():
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '7',
            '-keyout', str(DATA / 'key.pem'), '-out', str(DATA / 'ca.pem'), '-subj', '/CN=OpenAVC example product',
            '-addext', f'subjectAltName=IP:127.0.0.1,IP:{address},DNS:{socket.gethostname()}'],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (DATA / 'server.pem').write_bytes((DATA / 'ca.pem').read_bytes() + (DATA / 'key.pem').read_bytes())
        (DATA / 'management.token').write_text(secrets.token_hex(32))
    for name in ('key.pem', 'server.pem', 'management.token', 'login.json'):
        (DATA / name).chmod(0o600)
    model = str(ROOT / 'build/generated/model.json')
    hardware_log = (DATA / 'hardware.log').open('a')
    hardware = await asyncio.create_subprocess_exec(str(ROOT / 'build/example-c'), model, '-',
        env={**os.environ, 'OPENAVC_PRODUCT_SOCKET': str(DATA / 'hardware.sock')},
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=hardware_log)
    if (await asyncio.wait_for(hardware.stdout.readline(), 10)).strip() != b'READY 0':
        raise RuntimeError('The example hardware process did not start. Read /data/hardware.log.')
    config = {'identity': identity, 'data_dir': str(DATA / 'openavc'), 'model': model,
        'management_token_file': str(DATA / 'management.token'), 'device_service': str(ROOT / 'build/openavc-device-service'),
        'bridge_path': str(DATA / 'hardware.sock'), 'certificate': str(DATA / 'server.pem'),
        'ca_file': str(DATA / 'ca.pem'), 'login': credentials, 'example_ui': True, 'example_bind': '0.0.0.0',
        'example_server_port': int(os.environ.get('EXAMPLE_SERVER_PORT', '8080')),
        'server_environment': {'OPENAVC_PORT': '8080', 'OPENAVC_BIND': '0.0.0.0',
            'OPENAVC_CLOUD_ENABLED': 'false', 'OPENAVC_PORT80_REDIRECT': 'false',
            'SSL_CERT_FILE': str(DATA / 'trust.pem')}}
    if os.environ.get('EXAMPLE_INCLUDE_SERVER') == '1':
        config.update(server_python=sys.executable, firmware_version='SDK example')
    (DATA / 'product.json').write_text(json.dumps(config, indent=2))
    (DATA / 'product.json').chmod(0o600)
    lock = asyncio.Lock()

    async def physical(reader, writer):
        # Development instrumentation for this invented amplifier only. It is
        # loopback-bound and is not part of the firmware integration API.
        try:
            command = (await asyncio.wait_for(reader.readline(), 2)).decode().strip()
            if command not in {'snapshot', 'readfail 0', 'readfail 1', 'refuse 0', 'refuse 1'} and not command.startswith(('external ', 'delay ')):
                raise ValueError('Use a documented example-hardware operation.')
            async with lock:
                hardware.stdin.write((command + '\n').encode())
                await hardware.stdin.drain()
                line = await asyncio.wait_for(hardware.stdout.readline(), 2)
            writer.write(line)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    instrument = await asyncio.start_server(physical, '127.0.0.1', 9020)
    try:
        await Manager(config).run()
    finally:
        instrument.close()
        await instrument.wait_closed()
        if hardware.returncode is None:
            hardware.stdin.write(b'stop\n')
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await hardware.stdin.drain()
            try:
                await asyncio.wait_for(hardware.wait(), 10)
            except TimeoutError:
                hardware.kill()
                await hardware.wait()
        hardware_log.close()


if __name__ == '__main__':
    asyncio.run(main())
