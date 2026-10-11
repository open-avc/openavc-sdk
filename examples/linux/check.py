"""Check staged firmware startup, persistence and shutdown on an isolated Linux host."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from stage import ROOT, parser, stage

sys.path.insert(0, str(ROOT))
from product_checks import Client, read_json  # noqa: E402


def check(args):
    evidence = args.out.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    evidence.chmod(0o755)
    args.out = evidence / 'product'
    if os.getuid() == 0 and args.service_user is None:
        args.service_user = 'nobody'
    temporary_runtime = Path(tempfile.mkdtemp(prefix='oavc-fw-', dir='/tmp')) if not args.runtime_dir else None
    if temporary_runtime:
        temporary_runtime.chmod(0o755)
        args.runtime_dir = temporary_runtime / 'run'
    metadata = stage(args)
    account = pwd.getpwnam(metadata['service_user'])
    config = read_json(args.out / 'private/openavc.json')
    processes = []
    streams = []
    report = {'format': 1, 'status': 'running', 'checks': [], 'staging': metadata,
              'systemd_units': 'not_started', 'service_uid': account.pw_uid}

    def wait_for(probe):
        deadline = time.monotonic() + 50
        while True:
            result = probe()
            if result:
                return result
            if time.monotonic() >= deadline:
                raise ValueError('Firmware example did not reach the expected state. Read the local process logs.')
            time.sleep(.1)

    def launch(name, command, environment):
        stream = (evidence / (name + '.log')).open('w')
        streams.append(stream)
        identity = {'user': account.pw_uid, 'group': account.pw_gid, 'extra_groups': []} if os.getuid() == 0 else {}
        process = subprocess.Popen(command, cwd=args.out / 'data', env=environment,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stream, **identity)
        processes.append(process)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(50) or not process.stdout.readline().startswith(b'READY'):
                raise ValueError(name + ' did not start. Read its local log.')
        return process

    def stop(process):
        if process.poll() is None:
            process.terminate()
        try:
            output, _ = process.communicate(timeout=45)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise ValueError('A firmware process did not stop cleanly.') from None
        if process.returncode != 0:
            raise ValueError('A firmware process exited unsuccessfully.')
        return output

    control = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    token = (args.out / 'private/management.token').read_text().strip()
    def management(payload=None):
        request = urllib.request.Request('http://127.0.0.1:' + str(args.management_port) + '/oem/settings',
            data=json.dumps(payload).encode() if payload else None,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with control.open(request, timeout=50) as response:
            return json.load(response)

    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
                   'TMPDIR': str(Path(metadata['runtime_directory']) / 'tmp'),
                   'LD_LIBRARY_PATH': str(args.out / 'program/lib')}
    hardware_command = [str(args.out / 'program/hardware'), config['model'], '-', '--daemon']
    hardware_environment = {**environment, 'OPENAVC_PRODUCT_SOCKET': config['bridge_path'],
                            'OPENAVC_DEVICE_ID': config['identity']}
    manager_command = [str(args.python), '-m', 'openavc_oem', '--config', str(args.out / 'private/openavc.json')]
    try:
        hardware = launch('hardware-1', hardware_command, hardware_environment)
        manager = launch('management-1', manager_command, environment)
        client = Client('https://127.0.0.1:' + str(args.equipment_port), config['login'], config['ca_file'])
        def available():
            status, value = client.request('/v1/description')
            return value if status == 200 else None
        first = wait_for(available)
        state = management()
        assert not state['server'] and state['equipment']['remote_access_active']
        report['checks'].append('remote_equipment_started_without_terminal')
        result, outcome = client.request('/v1/command', {'session': first['session'], 'id': 1, 'command': 'increment', 'params': {}})
        assert result == 200 and outcome['outcome'] == 'completed'
        wait_for(lambda: client.request('/v1/snapshot')[1]['state']['counter'] == 1)
        report['checks'].append('hardware_command_and_readback')
        stopped = stop(hardware)
        assert b'CLOSED' in stopped and b'STATE 1 1 0 ' in stopped
        wait_for(lambda: client.request('/v1/snapshot')[0] == 503)
        report['checks'].append('graceful_hardware_stop_and_unavailable_readback')
        hardware = launch('hardware-2', hardware_command, hardware_environment)
        second = wait_for(available)
        assert first['session'] != second['session']
        assert client.request('/v1/snapshot')[1]['state']['counter'] == 0
        report['checks'].append('hardware_restart_changes_session_without_replay')
        if args.with_server:
            state = management({'revision': state['revision'], 'field': 'server', 'enabled': True})
            assert state['server_running'] and state['equipment']['reserved_locally']
            assert not state['equipment']['remote_access_active'] and not state['equipment']['discovery_active']
            report['checks'].append('full_server_starts_and_reserves_hardware')
        marker = args.out / 'data/firmware-check-marker'
        marker.write_text('retained\n')
        stop(manager)
        assert hardware.poll() is None
        manager = launch('management-2', manager_command, environment)
        state = management()
        assert state['settings_valid'] and marker.read_text() == 'retained\n'
        assert state['server_running'] == args.with_server
        report['checks'].append('management_restart_retains_settings_and_data')
        stop(manager)
        assert hardware.poll() is None
        stop(hardware)
        assert not Path(config['bridge_path']).exists()
        report['checks'].append('ordered_shutdown_leaves_no_hardware_socket')
        for name, sha in metadata['program_sha256'].items():
            assert hashlib.sha256((args.out / name).read_bytes()).hexdigest() == sha
        report['checks'].append('program_files_unchanged')
        report['status'] = 'passed'
    except BaseException:
        report['status'] = 'failed'
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                try:
                    stop(process)
                except ValueError:
                    report['status'] = 'failed'
        for stream in streams:
            stream.close()
        if temporary_runtime and report['status'] == 'passed':
            shutil.rmtree(temporary_runtime)
        (evidence / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Firmware example passed. Report: ' + str(evidence / 'report.json'))


if __name__ == '__main__':
    command = parser()
    try:
        check(command.parse_args())
    except ValueError as error:
        command.exit(1, str(error) + '\n')
    except Exception:
        command.exit(1, 'Firmware example failed. Read report.json and the local process logs.\n')
