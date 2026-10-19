"""Check staged firmware startup, persistence and shutdown on an isolated Linux host."""
import hashlib
import base64
import json
import os
from pathlib import Path
import pwd
import selectors
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request

from stage import ROOT, parser, stage

sys.path.insert(0, str(ROOT))
from product_checks import Client, read_json  # noqa: E402


def check(args):
    candidate_options = (args.candidate_python, args.candidate_bundle, args.installed_bundle)
    candidate_check = any(value is not None for value in candidate_options)
    if candidate_check and (not all(value is not None for value in candidate_options)
                            or not args.with_server or args.update or args.storage_recovery):
        raise ValueError('For candidate assessment, supply --with-server, --installed-bundle, '
                         '--candidate-python and --candidate-bundle; run other update/recovery exercises separately.')
    alternate = ROOT / ('build/example-cpp' if args.language == 'c' else 'build/example-c')
    if args.update and not alternate.is_file():
        raise ValueError('For --update, build both C and C++ examples from a source checkout first. See docs/firmware.md.')
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
    token = Path(config['management_token_file']).read_text().strip()
    def management(payload=None, path='/oem/settings', credential=token):
        request = urllib.request.Request('http://127.0.0.1:' + str(args.management_port) + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer ' + credential, 'Content-Type': 'application/json'})
        with control.open(request, timeout=50) as response:
            return json.load(response)

    def copy_stopped_tree(source, destination):
        # Only used after this fixture's owned writers have stopped. Preserve
        # the service account's ownership when the staging tool runs as root.
        shutil.copytree(source, destination, symlinks=True)
        if os.getuid() == 0:
            for original in [source, *source.rglob('*')]:
                copied = destination / original.relative_to(source)
                info = original.lstat()
                os.chown(copied, info.st_uid, info.st_gid, follow_symlinks=False)

    server_account = {'username': 'firmware-engineer', 'password': secrets.token_urlsafe(24)}
    next_server_request = 0.0
    def server_api(path, payload=None, method=None, headers=None):
        nonlocal next_server_request
        time.sleep(max(0, next_server_request - time.monotonic()))
        next_server_request = time.monotonic() + 1.1
        basic = base64.b64encode((server_account['username'] + ':' + server_account['password']).encode()).decode()
        request = urllib.request.Request('http://127.0.0.1:' + str(args.server_port) + path,
            data=json.dumps(payload).encode() if payload is not None else None, method=method,
            headers={'Authorization': 'Basic ' + basic, 'Content-Type': 'application/json', **(headers or {})})
        with control.open(request, timeout=30) as response:
            return json.load(response), response.headers.get('ETag')

    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
                   'TMPDIR': str(Path(metadata['runtime_directory']) / 'tmp'),
                   'LD_LIBRARY_PATH': str(args.out / 'program/lib')}
    hardware_command = [str(args.out / 'program/hardware'), config['model'], '-', '--daemon']
    hardware_environment = {**environment, 'OPENAVC_PRODUCT_SOCKET': config['bridge_path'],
                            'OPENAVC_DEVICE_ID': config['identity']}
    manager_command = [str(args.python), '-m', 'openavc_oem', '--config', str(args.out / 'private/openavc.json')]
    candidate = None
    if candidate_check:
        from candidate_check import CandidateCheck
        candidate = CandidateCheck(args, evidence, args.out / 'private/openavc.json', environment)
        environment = candidate.environment
    def observe_reconnect(name):
        log = (evidence / (name + '.log')).open('w')
        streams.append(log)
        process = subprocess.Popen([str(args.python), str(ROOT / 'check-product.py'),
            '--url', 'https://127.0.0.1:' + str(args.equipment_port),
            '--credentials', str(args.out / 'private/product-credentials.json'),
            '--ca-file', config['ca_file'], '--model', config['model'],
            '--reconnect', '45', '--out', str(evidence / name)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        def observed(expected):
            if process.poll() is not None:
                raise ValueError('Reconnect observer stopped early. Read ' + name + '.log.')
            report_path = evidence / name / 'report.json'
            return report_path.exists() and read_json(report_path).get('reconnect', {}).get('status') == expected
        wait_for(lambda: observed('waiting_for_disconnect'))
        return process, observed

    def recovered(process, name):
        if process.wait(timeout=50) != 0:
            raise ValueError('Reconnect observation failed. Read ' + name + '.log.')
        result = read_json(evidence / name / 'report.json')
        assert result['status'] == 'passed' and not result['commands'] and not result['refusals']
        assert result['identity'] == config['identity']
        report['checks'].append(name)

    def project_backup(action):
        # Use the matching core's existing project backup scope with all fixture
        # writers stopped. Do not restore credentials, cloud identity or settings
        # by replacing the server's entire data directory.
        code = (
            'from pathlib import Path; import shutil,sys; '
            'from openavc.core.backup_manager import create_backup,restore_from_backup; '
            'project=Path(sys.argv[2]); archive=Path(sys.argv[3]); '
            'shutil.copy2(create_backup(project,"Firmware example"),archive) '
            'if sys.argv[1]=="create" else restore_from_backup(archive,project)'
        )
        identity = {'user': account.pw_uid, 'group': account.pw_gid, 'extra_groups': []} if os.getuid() == 0 else {}
        with (evidence / ('project-' + action + '.log')).open('w') as log:
            subprocess.run([str(args.python), '-c', code, action, str(args.out / 'data/server/project.avc'),
                            str(args.out / 'data/firmware-example-backup.zip')],
                           cwd=args.out / 'data', env={**environment,
                               'OPENAVC_EQUIPMENT_MANIFEST': str(args.out / 'data/equipment.json')},
                           **identity, check=True, stdout=log, stderr=subprocess.STDOUT)
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
        with (evidence / 'product-checks.log').open('w') as log:
            subprocess.run([str(args.python), str(ROOT / 'check-product.py'),
                            '--url', 'https://127.0.0.1:' + str(args.equipment_port),
                            '--credentials', str(args.out / 'private/product-credentials.json'),
                            '--ca-file', config['ca_file'], '--model', config['model'],
                            '--checks', str(ROOT / 'sample/checks.json'),
                            '--out', str(evidence / 'product-checks')],
                           check=True, stdout=log, stderr=subprocess.STDOUT)
        wait_for(lambda: client.request('/v1/snapshot')[1]['state']['counter'] == 2)
        report['checks'].append('product_checker_refusals_commands_and_readback')
        observer, observed = observe_reconnect('product-hardware-reconnect')
        stopped = stop(hardware)
        (evidence / 'hardware-first-stop.log').write_bytes(stopped)
        assert b'CLOSED' in stopped and b'STATE 3 3 0 ' in stopped
        wait_for(lambda: client.request('/v1/snapshot')[0] == 503)
        report['checks'].append('graceful_hardware_stop_and_unavailable_readback')
        wait_for(lambda: observed('waiting_for_recovery'))
        hardware = launch('hardware-2', hardware_command, hardware_environment)
        second = wait_for(available)
        assert first['session'] != second['session']
        assert client.request('/v1/snapshot')[1]['state']['counter'] == 0
        report['checks'].append('hardware_restart_changes_session_without_replay')
        recovered(observer, 'product-hardware-reconnect')
        observer, observed = observe_reconnect('product-management-reconnect')
        stop(manager)
        wait_for(lambda: observed('waiting_for_recovery'))
        manager = launch('management-reconnect', manager_command, environment)
        recovered(observer, 'product-management-reconnect')
        if args.with_server:
            state = management({'revision': state['revision'], 'field': 'server', 'enabled': True})
            assert state['server_running'] and state['equipment']['reserved_locally']
            assert not state['equipment']['remote_access_active'] and not state['equipment']['discovery_active']
            report['checks'].append('full_server_starts_and_reserves_hardware')
        if (args.update or args.storage_recovery or candidate_check) and args.with_server:
            wait_for(lambda: management()['server_responsive'])
            server_api('/api/auth/setup', server_account)
            (evidence / 'server-login.json').write_text(json.dumps(server_account) + '\n')
            (evidence / 'server-login.json').chmod(0o600)
            project, etag = server_api('/api/project')
            project['variables'].append({'id': 'firmware_check', 'type': 'number', 'default': 0, 'persist': True})
            server_api('/api/project', project, 'PUT', {'If-Match': etag})
            server_api('/api/state/var.firmware_check', {'value': 17}, 'PUT')
        marker = args.out / 'data/firmware-check-marker'
        marker.write_text('retained\n')
        stop(manager)
        assert hardware.poll() is None
        if candidate:
            candidate.before_manager_launch()
        manager = launch('management-2', manager_command, environment)
        state = management()
        assert state['settings_valid'] and marker.read_text() == 'retained\n'
        assert state['server_running'] == args.with_server
        report['checks'].append('management_restart_retains_settings_and_data')
        if args.storage_recovery:
            private_hashes = {str(p.relative_to(args.out / 'private')): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (args.out / 'private').rglob('*') if p.is_file()}
            report['storage_recovery'] = []
            for fault in ('truncated_settings', 'interrupted_save_marker'):
                before = management()
                stop(manager)
                assert hardware.poll() is None
                data = args.out / 'data'
                policy = data / 'management.json'
                original = policy.read_bytes()
                (evidence / (fault + '-original.json')).write_bytes(original)
                broken = policy if fault == 'truncated_settings' else data / 'management.pending'
                payload = b'{"format":' if fault == 'truncated_settings' else json.dumps({
                    'identity': config['identity'], 'revision': before['revision']}).encode()
                broken.write_bytes(payload)
                broken.chmod(0o600)
                if os.getuid() == 0:
                    os.chown(broken, account.pw_uid, account.pw_gid)
                manager = launch('management-' + fault, manager_command, environment)
                refused = management()
                assert not refused['settings_valid'] and refused['error']
                assert not refused['server_running'] and not refused['equipment']['remote_access_active']
                assert not refused['equipment']['discovery_active']
                assert hardware.poll() is None and marker.read_text() == 'retained\n'
                assert client.request('/v1/description')[0] == 503
                assert client.request('/v1/command', {'session': second['session'], 'id': 2,
                    'command': 'increment', 'params': {}})[0] == 503
                for attempt, expected in ((lambda: management({'revision': refused['revision'],
                        'field': 'equipment', 'enabled': True}), 409),
                        (lambda: management({}, '/oem/repair', credential=''), 401)):
                    try:
                        attempt()
                    except urllib.error.HTTPError as error:
                        assert error.code == expected
                    else:
                        raise ValueError('Damaged settings accepted an unauthorized or unsafe operation.')
                repaired = management({}, '/oem/repair')
                flags = ('server', 'programmer', 'panel', 'discovery', 'remote_access')
                assert repaired['settings_valid'] and repaired['identity'] == config['identity']
                assert not any(repaired[key] for key in flags)
                assert client.request('/v1/description')[0] == 403
                retained = list(data.glob('management-invalid-*.json')) if fault == 'truncated_settings' else list(data.glob('management-pending-invalid-*.json'))
                assert any(path.read_bytes() == payload for path in retained)
                assert not (data / 'management.pending').exists()
                # Repair restores no permission implicitly, including after boot.
                stop(manager)
                assert hardware.poll() is None
                manager = launch('management-' + fault + '-repaired', manager_command, environment)
                repaired = management()
                assert repaired['settings_valid'] and not any(repaired[key] for key in flags)
                for field in flags:
                    if before[field]:
                        repaired = management({'revision': repaired['revision'], 'field': field, 'enabled': True})
                if args.with_server:
                    wait_for(lambda: management()['server_responsive'])
                    assert server_api('/api/auth/required')[0]['state'] == 'required'
                    assert server_api('/api/state/var.firmware_check')[0]['value'] == 17
                    wait_for(lambda: server_api('/api/devices/local')[0]['connected'])
                    assert server_api('/api/devices/local')[0]['state']['counter'] == 0
                    assert client.request('/v1/description')[0] == 403
                else:
                    description = wait_for(available)
                    assert description['identity'] == config['identity']
                    assert client.request('/v1/snapshot')[1]['state']['counter'] == 0
                for name, sha in private_hashes.items():
                    assert hashlib.sha256((args.out / 'private' / name).read_bytes()).hexdigest() == sha
                report['storage_recovery'].append({'fault': fault, 'refused_remote_command_status': 503,
                    'unauthenticated_repair_status': 401, 'hardware_process_id': hardware.pid,
                    'hardware_process_unchanged': True,
                    'private_configuration_unchanged': True, 'permissions_disabled_after_repair_and_restart': True,
                    'account_and_saved_variable_retained': args.with_server, 'damaged_input_retained': True})
                report['checks'].append(fault + '_refused_control_and_recovered_without_resetting_product')
        if candidate:
            wait_for(lambda: management().get('server_responsive')
                     and management()['process_logs']['queued_chunks'] == 0)
            report['candidate_assessment'] = candidate.exercise(manager, hardware, management)
            report['checks'].append('candidate_assessed_and_changed_source_refused_before_stop')
        stop(manager)
        assert hardware.poll() is None
        stopped = stop(hardware)
        (evidence / 'hardware-final-stop.log').write_bytes(stopped)
        if args.storage_recovery:
            assert b'STATE 0 0 0 ' in stopped
            report['checks'].append('hardware_counter_confirms_refused_commands_were_not_executed')
        assert not Path(config['bridge_path']).exists()
        report['checks'].append('ordered_shutdown_leaves_no_hardware_socket')
        if args.update:
            private_hashes = {str(p.relative_to(args.out / 'private')): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (args.out / 'private').rglob('*') if p.is_file()}
            data_before = evidence / 'data-before-update'
            if args.with_server:
                project_backup('create')
            copy_stopped_tree(args.out / 'data', data_before)
            program = args.out / 'program'
            # Keep directory renames within one parent: an unprivileged updater
            # cannot move a read-only directory's '..' entry to another parent.
            candidate = args.out / 'program-next'
            shutil.copytree(program, candidate)
            candidate.chmod(0o755)
            (candidate / 'hardware').unlink()
            shutil.copy2(alternate, candidate / 'hardware')
            (candidate / 'hardware').chmod(0o555)
            candidate.chmod(0o555)
            candidate_sha = hashlib.sha256((candidate / 'hardware').read_bytes()).hexdigest()
            assert candidate_sha != metadata['program_sha256']['program/hardware']
            program.rename(args.out / 'program-before-update')
            candidate.rename(program)
            hardware = launch('hardware-updated', hardware_command, hardware_environment)
            manager = launch('management-updated', manager_command, environment)
            updated = management()
            assert updated['server_running'] == args.with_server and updated['settings_valid']
            assert marker.read_text() == 'retained\n'
            if args.with_server:
                wait_for(lambda: management()['server_responsive'])
                assert server_api('/api/auth/required')[0]['state'] == 'required'
                assert server_api('/api/state/var.firmware_check')[0]['value'] == 17
                wait_for(lambda: server_api('/api/devices/local')[0]['connected'])
                server_api('/api/devices/local/command', {'command': 'increment', 'params': {}})
                wait_for(lambda: server_api('/api/devices/local')[0]['state'].get('counter') == 1)
                server_api('/api/state/var.firmware_check', {'value': 29}, 'PUT')
            else:
                description = wait_for(available)
                result, outcome = client.request('/v1/command', {'session': description['session'], 'id': 1,
                    'command': 'increment', 'params': {}})
                assert result == 200 and outcome['outcome'] == 'completed'
                wait_for(lambda: client.request('/v1/snapshot')[1]['state']['counter'] == 1)
            marker.write_text('changed after update\n')
            management({'revision': updated['revision'], 'field': 'programmer', 'enabled': False})
            report['checks'].append('replacement_application_runs_with_retained_identity_settings_and_data')
            stop(manager)
            assert b'STATE 1 1 0 ' in stop(hardware)
            copy_stopped_tree(args.out / 'data', evidence / 'data-after-update')
            if args.with_server:
                project_backup('restore')
            shutil.copy2(data_before / 'firmware-check-marker', marker)
            program.rename(args.out / 'program-after-update')
            (args.out / 'program-before-update').rename(program)
            hardware = launch('hardware-rollback', hardware_command, hardware_environment)
            manager = launch('management-rollback', manager_command, environment)
            restored = management()
            assert restored['server_running'] == args.with_server and restored['settings_valid']
            assert restored['programmer'] is False
            restored = management({'revision': restored['revision'], 'field': 'programmer', 'enabled': updated['programmer']})
            assert marker.read_text() == 'retained\n'
            if args.with_server:
                wait_for(lambda: management()['server_responsive'])
                assert server_api('/api/state/var.firmware_check')[0]['value'] == 17
                wait_for(lambda: server_api('/api/devices/local')[0]['connected'])
                assert server_api('/api/devices/local')[0]['state']['counter'] == 0
                server_api('/api/devices/local/command', {'command': 'increment', 'params': {}})
                wait_for(lambda: server_api('/api/devices/local')[0]['state'].get('counter') == 1)
            else:
                description = wait_for(available)
                assert client.request('/v1/snapshot')[1]['state']['counter'] == 0
                result, outcome = client.request('/v1/command', {'session': description['session'], 'id': 1,
                    'command': 'increment', 'params': {}})
                assert result == 200 and outcome['outcome'] == 'completed'
                wait_for(lambda: client.request('/v1/snapshot')[1]['state']['counter'] == 1)
            stop(manager)
            assert b'STATE 1 1 0 ' in stop(hardware)
            for name, sha in private_hashes.items():
                assert hashlib.sha256((args.out / 'private' / name).read_bytes()).hexdigest() == sha
            report['application_update'] = {'original_hardware_sha256': metadata['program_sha256']['program/hardware'],
                'candidate_hardware_sha256': candidate_sha, 'private_configuration_unchanged': True,
                'snapshot_taken_with_writers_stopped': True, 'same_core_and_data_format': True,
                'saved_variable_restored': args.with_server, 'current_access_setting_preserved': True}
            report['checks'].append('rollback_restores_application_and_pre_update_data_without_command_replay')
        for name, sha in metadata['program_sha256'].items():
            assert hashlib.sha256((args.out / name).read_bytes()).hexdigest() == sha
        report['checks'].append('program_files_unchanged')
        report['status'] = 'passed'
    except BaseException:
        report['status'] = 'failed'
        (evidence / 'failure.log').write_text(traceback.format_exc())
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
    command.add_argument('--update', action='store_true', help='From a source checkout, replace the hardware example and roll back its program/project data with writers stopped.')
    command.add_argument('--storage-recovery', action='store_true', help='Damage only this disposable example\'s settings and check authenticated repair, retained data and disabled access.')
    command.add_argument('--installed-bundle', type=Path, help='Trusted complete bundle installed in --python for candidate assessment')
    command.add_argument('--candidate-python', type=Path, help='Separately installed candidate Python environment; assessment only')
    command.add_argument('--candidate-bundle', type=Path, help='Trusted complete candidate bundle; requires --with-server and --installed-bundle')
    try:
        check(command.parse_args())
    except ValueError as error:
        command.exit(1, str(error) + '\n')
    except Exception:
        command.exit(1, 'Firmware example failed. Read report.json and the local process logs.\n')
