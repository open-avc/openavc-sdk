"""Terminate only the selected example controller after hardware accepts work."""
import json
import subprocess
import time


def check_controller_crash(layout, report, *, api, docker, physical, until, recorded, product_status):
    controller = layout['controller']
    labels = json.loads(docker('inspect', '-f', '{{json .Config.Labels}}', controller))
    if labels.get('openavc.sdk.example') != layout['network']:
        raise ValueError('Controller crash checks require an owned example container.')
    embedded = layout['arrangement'] == 'embedded'
    target = 'local' if embedded else 'product1'
    original_identity = api('/api/devices/' + target)['state']['_sdk_identity']
    original_ui = api('/api/project')['ui']
    core = None
    if embedded:
        # The supervisor recorded the exact child it launched. Verify that
        # record against the live PID and birth time before sending a signal.
        code = ("import json,pathlib,psutil; "
                "events=[json.loads(line) for line in pathlib.Path('/data/openavc/logs/recovery.jsonl').read_text().splitlines()]; "
                "event=next(e for e in reversed(events) if e['event']=='launched'); "
                "p=psutil.Process(event['pid']); "
                "assert p.ppid()==1 and p.cmdline()[-2:]==['-m','openavc_oem.server']; "
                "print(json.dumps({'pid':p.pid,'created':p.create_time()}))")
        core = json.loads(docker('exec', controller, 'python', '-c', code))
    before = physical('product1')
    physical('product1', 'delay 10000')
    response = api('/api/devices/' + target + '/command', {'command': 'increment', 'params': {}}, expected=409)
    assert response['outcome'] == 'uncertain'
    accepted = physical('product1')
    assert accepted['callbacks'] == before['callbacks'] + 1
    assert accepted['writes'] == before['writes'] and accepted['counter'] == before['counter']
    if embedded:
        code = (f"import psutil,signal; p=psutil.Process({core['pid']}); "
                f"assert p.create_time()=={core['created']!r} and p.ppid()==1 and p.cmdline()[-2:]==['-m','openavc_oem.server']; "
                "p.send_signal(signal.SIGKILL)")
        docker('exec', controller, 'python', '-c', code)
        until(lambda: not product_status('product1')['server_running'])
        host = product_status('product1')
        assert host['server'] and host['equipment']['reserved_locally']
        assert not host['equipment']['discovery_active'] and not host['equipment']['remote_access_active']
    else:
        docker('kill', '--signal', 'KILL', controller)
        assert docker('inspect', '-f', '{{.State.Running}}', controller) == 'false'
    for name in layout['products']:
        if not embedded or name != 'product1':
            assert not product_status(name)['server_running']
    completed = until(lambda: (state if (state := physical('product1'))['counter'] > before['counter'] else None), timeout=30)
    assert completed['counter'] == before['counter'] + 1 and completed['writes'] == before['writes'] + 1
    assert completed['callbacks'] == accepted['callbacks']
    recorded('Accepted hardware work completes once after the selected controller is forcibly terminated')
    if embedded:
        until(lambda: product_status('product1')['server_running'], timeout=60)
    else:
        docker('start', controller)
    # A running process is not server readiness. Poll only health; never
    # resend the interrupted command to discover whether startup finished.
    code = ("import sys,urllib.error,urllib.request\n"
            "try:\n"
            "    print(urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=1).status)\n"
            "except (urllib.error.URLError, OSError):\n"
            "    sys.exit(1)\n")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if docker('exec', controller, 'python', '-c', code) == '200':
                break
        except subprocess.CalledProcessError:
            pass
        time.sleep(1)
    else:
        raise AssertionError('The restarted controller did not become healthy within 60 seconds.')
    until(lambda: api('/api/devices/' + target)['connected'], timeout=90)
    recovered_device = until(lambda: (item if (item := api('/api/devices/' + target))['state'].get('counter') == completed['counter'] else None))
    assert recovered_device['state']['_sdk_identity'] == original_identity
    assert api('/api/project')['ui'] == original_ui
    time.sleep(3)
    recovered = physical('product1')
    assert recovered['counter'] == completed['counter'] and recovered['writes'] == completed['writes']
    assert recovered['callbacks'] == accepted['callbacks']
    api('/api/devices/' + target + '/command', {'command': 'increment', 'params': {}})
    fresh = physical('product1')
    assert fresh['counter'] == completed['counter'] + 1 and fresh['writes'] == completed['writes'] + 1
    report['observations']['controller_crash'] = {'caller_outcome': response['outcome'],
        'hardware_delay_ms': 10000, 'post_reconnect_observation_seconds': 3,
        'embedded_automatic_restart': embedded, 'terminated_child': core,
        'hardware_before': before, 'hardware_accepted': accepted,
        'hardware_completed': completed, 'hardware_after_reconnect': recovered,
        'hardware_after_new_command': fresh, 'project_ui_unchanged': True,
        'hardware_process_terminated': False}
    recorded('Controller recovery retains account access and programming without replaying accepted hardware work')
