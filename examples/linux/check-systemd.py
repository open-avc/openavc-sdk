"""Exercise the example systemd services on an isolated development host."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

from stage import ROOT, parser, stage

sys.path.insert(0, str(ROOT))
from product_checks import Client, read_json  # noqa: E402


def check(args):
    if not Path('/run/systemd/system').is_dir():
        raise ValueError('Use a Linux development host running systemd.')
    prefix = [] if os.getuid() == 0 else ['sudo', '-n']
    hardware = 'openavc-product-hardware.service'
    management = 'openavc-product-management.service'

    def run(*command):
        return subprocess.check_output([*prefix, *command], text=True, stderr=subprocess.STDOUT, timeout=90)

    def property_value(unit, field):
        return run('systemctl', 'show', unit, '--property=' + field, '--value').strip()

    for unit in (hardware, management):
        loaded = subprocess.run([*prefix, 'systemctl', 'show', unit, '--property=LoadState', '--value'],
                                capture_output=True, text=True, timeout=15)
        if loaded.stdout.strip() != 'not-found':
            if loaded.returncode:
                raise ValueError('Could not inspect systemd. Check passwordless sudo and service-manager access.')
            raise ValueError('Example service names already exist. Use an isolated host without these services.')
    evidence = args.out.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    args.out = evidence / 'product'
    if os.getuid() == 0 and args.service_user is None:
        args.service_user = 'nobody'
    metadata = stage(args)
    config = read_json(args.out / 'private/openavc.json')
    token = (args.out / 'private/management.token').read_text().strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    client = Client('https://127.0.0.1:' + str(args.equipment_port), config['login'], config['ca_file'])
    report = {'format': 1, 'status': 'running', 'staging': metadata, 'checks': []}
    linked = []

    def status(payload=None):
        request = urllib.request.Request('http://127.0.0.1:' + str(args.management_port) + '/oem/settings',
            data=json.dumps(payload).encode() if payload else None,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            with opener.open(request, timeout=50) as response:
                return json.load(response)
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError:
            return None

    def wait_for(probe):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            value = probe()
            if value:
                return value
            time.sleep(.1)
        raise ValueError('The systemd example did not reach the expected state.')

    try:
        run('systemd-analyze', 'verify', *(str(args.out / 'systemd' / unit) for unit in (hardware, management)))
        for unit in (hardware, management):
            run('systemctl', 'link', '--runtime', str(args.out / 'systemd' / unit))
            linked.append(unit)
        run('systemctl', 'start', management)
        wait_for(status)
        def description():
            code, value = client.request('/v1/description')
            return value if code == 200 else None
        first = wait_for(description)
        report['checks'].append('ordered_services_start')
        pid = property_value(management, 'MainPID')
        program_options = run('nsenter', '--target', pid, '--mount', 'findmnt', '-n', '-o', 'OPTIONS', '-T', config['model']).strip().split(',')
        data_options = run('nsenter', '--target', pid, '--mount', 'findmnt', '-n', '-o', 'OPTIONS', '-T', config['data_dir']).strip().split(',')
        assert 'ro' in program_options and 'rw' in data_options
        report['checks'].append('program_read_only_and_data_writable_in_service_namespace')
        run('systemctl', 'stop', hardware)
        assert property_value(hardware, 'ExecMainStatus') == '0'
        wait_for(lambda: client.request('/v1/snapshot')[0] == 503)
        assert property_value(management, 'ActiveState') == 'active'
        run('systemctl', 'start', hardware)
        second = wait_for(description)
        assert first['session'] != second['session']
        report['checks'].append('hardware_restart_reports_new_session')
        state = status()
        if args.with_server:
            state = status({'revision': state['revision'], 'field': 'server', 'enabled': True})
            assert state['server_running'] and state['equipment']['reserved_locally']
            report['checks'].append('full_server_starts_with_protected_filesystem')
        hardware_pid = property_value(hardware, 'MainPID')
        run('systemctl', 'restart', management)
        def restored():
            value = status()
            return value if value and value['server_running'] == args.with_server else None
        state = wait_for(restored)
        assert state['settings_valid'] and property_value(hardware, 'MainPID') == hardware_pid
        report['checks'].append('management_restart_preserves_hardware_and_saved_server_setting')
        run('systemctl', 'stop', management, hardware)
        assert property_value(management, 'ExecMainStatus') == '0' and property_value(hardware, 'ExecMainStatus') == '0'
        assert not Path(config['bridge_path']).exists()
        for name, sha in metadata['program_sha256'].items():
            assert hashlib.sha256((args.out / name).read_bytes()).hexdigest() == sha
        report['checks'].append('ordered_shutdown_and_unchanged_program_files')
        report['status'] = 'passed'
    except BaseException:
        report['status'] = 'failed'
        raise
    finally:
        for unit in reversed(linked):
            try:
                run('systemctl', 'stop', unit)
                (evidence / (unit + '.log')).write_text(run('journalctl', '-u', unit, '--no-pager', '-n', '200'))
                if property_value(unit, 'ActiveState') == 'failed':
                    run('systemctl', 'reset-failed', unit)
                run('systemctl', 'disable', '--runtime', unit)
            except subprocess.CalledProcessError:
                report['cleanup_warning'] = True
        run('systemctl', 'daemon-reload')
        (evidence / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if report.get('cleanup_warning'):
        raise ValueError('Inspect the example service cleanup before another run.')
    print('Systemd example passed. Report: ' + str(evidence / 'report.json'))


if __name__ == '__main__':
    command = parser()
    try:
        check(command.parse_args())
    except ValueError as error:
        command.exit(1, str(error) + '\n')
    except subprocess.CalledProcessError as error:
        command.exit(1, 'Service command failed:\n' + error.output + '\nRead the retained report and service logs.\n')
    except Exception:
        command.exit(1, 'Systemd example failed. Read the report and service logs in the selected output directory.\n')
