"""Verify the running invented equipment using normal OpenAVC API operations."""
import argparse
import base64
import json
from pathlib import Path
import secrets
import subprocess
import time
import urllib.error
import urllib.request


def check(folder):
    layout = json.loads((folder / 'run.json').read_text())
    login = json.loads((folder / 'product-login.json').read_text())
    account = {'username': 'example-admin', 'password': secrets.token_urlsafe(24)}
    (folder / 'server-login.json').write_text(json.dumps(account, indent=2) + '\n')
    (folder / 'server-login.json').chmod(0o600)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    authorization = 'Basic ' + base64.b64encode((account['username'] + ':' + account['password']).encode()).decode()
    report = {'status': 'running', 'arrangement': layout['arrangement'],
              'recovery': layout.get('recovery', False), 'faults': layout.get('faults', False),
              'product_count': len(layout['products']), 'image': layout['image'],
              'core_head': layout['contract']['core_head'], 'checks': [], 'observations': {}}
    request_interval = 1.1
    next_request = 0.0
    report['observations']['controller_requests'] = {'count': 0, 'minimum_interval_seconds': request_interval}

    def docker(*arguments):
        return subprocess.check_output(layout['docker'] + list(arguments), text=True, timeout=100).strip()

    def product_status(name, field=None, enabled=None):
        code = ("import json,pathlib,urllib.request; "
                "token=pathlib.Path('/data/management.token').read_text(); "
                "url='http://127.0.0.1:9081/oem/settings'; "
                "headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}; "
                "req=urllib.request.Request(url,headers=headers); "
                "state=json.load(urllib.request.urlopen(req,timeout=10)); ")
        if field:
            code += (f"body=json.dumps({{'revision':state['revision'],'field':{field!r},'enabled':{enabled!r}}}).encode(); "
                     "req=urllib.request.Request(url,headers=headers,data=body); "
                     "state=json.load(urllib.request.urlopen(req,timeout=60)); ")
        code += 'print(json.dumps(state))'
        return json.loads(docker('exec', layout['products'][name]['container'], 'python', '-c', code))

    def api(path, data=None, method=None, expected=200):
        nonlocal next_request
        # All these requests pass through the example's gateway. Pace the
        # observer below even the normal 60/minute API budget; the controller's
        # ordinary limits stay enabled. Never retry a hardware command.
        time.sleep(max(0.0, next_request - time.monotonic()))
        next_request = time.monotonic() + request_interval
        report['observations']['controller_requests']['count'] += 1
        request = urllib.request.Request(layout['server_url'] + path,
            data=json.dumps(data).encode() if data is not None else None, method=method,
            headers={'Content-Type': 'application/json', 'Authorization': authorization})
        try:
            response = opener.open(request, timeout=100)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            if response.status != expected:
                raise AssertionError(f'{path}: expected {expected}, received {response.status}: {raw.decode()}')
            return json.loads(raw)

    def until(callback, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = callback()
            if value:
                return value
            time.sleep(.1)
        raise AssertionError('Timed out waiting for expected hardware feedback.')

    def physical(name, command='snapshot'):
        code = ("import socket; s=socket.create_connection(('127.0.0.1',9020)); "
                f"s.sendall({(command + chr(10)).encode()!r}); print(s.makefile().readline().strip()); s.close()")
        output = subprocess.check_output(layout['docker'] + ['exec', layout['products'][name]['container'],
                                         'python', '-c', code], text=True, timeout=10).split()
        assert output[0] == 'STATE', output
        return {'writes': int(output[1]), 'callbacks': int(output[2]), 'wrong_threads': int(output[3]),
                'counter': int(output[5]), 'gain1': float(output[7]), 'gain2': float(output[8])}

    def equipment_probe(*arguments):
        return json.loads(docker('exec', layout['products']['product2']['container'],
                                 'python', 'examples/system/probe.py', *arguments))

    def recorded(name):
        report['checks'].append(name)
        (folder / 'checks.json').write_text(json.dumps(report, indent=2) + '\n')

    try:
        for entry in layout['products'].values():
            networks = json.loads(docker('inspect', '-f', '{{json .NetworkSettings.Networks}}', entry['container']))
            expected_bridge = layout.get('cloud_access', False) and entry['container'] == layout['controller']
            assert set(networks) == {layout['network'], *(['bridge'] if expected_bridge else [])}
        controller_networks = json.loads(docker('inspect', '-f', '{{json .NetworkSettings.Networks}}', layout['controller']))
        assert ('bridge' in controller_networks) == layout.get('cloud_access', False)
        roots_code = "import hashlib,json,ssl; print(json.dumps(sorted(hashlib.sha256(cert).hexdigest() for cert in ssl.create_default_context().get_ca_certs(binary_form=True))))"
        original_roots = set(json.loads(docker('exec', layout['ordinary'], 'python', '-c', roots_code)))
        controller_roots = set(json.loads(docker('exec', '-e', 'SSL_CERT_FILE=/data/trust.pem', layout['controller'], 'python', '-c', roots_code)))
        assert original_roots and original_roots <= controller_roots
        report['observations']['network'] = {'cloud_access': layout.get('cloud_access', False),
                                             'public_trust_roots_retained': len(original_roots)}
        recorded('Only the selected controller can have optional outbound access; normal public TLS trust is retained')
        assert api('/api/auth/required')['state'] == 'setup'
        api('/api/auth/setup', account)
        local = 'local' if layout['arrangement'] == 'embedded' else 'product1'
        devices = {name: local if name == 'product1' else name for name in layout['products']}
        wanted = {*devices.values(), 'ordinary'}
        until(lambda: wanted <= {device['id'] for device in api('/api/devices')['devices'] if device['connected']})
        recorded('All SDK products and ordinary TCP equipment connect')
        for name, product in layout['products'].items():
            status = product_status(name)
            running = name == 'product1' and layout['arrangement'] == 'embedded'
            assert status['server_installed'] == product['server_included']
            assert status['server_running'] == running and status['server'] == running
            assert status['equipment']['reserved_locally'] == running
        recorded('Only the selected product runs a server; installed peer servers remain stopped')
        for number, (name, device) in enumerate(devices.items(), 1):
            before = physical(name)
            gain = -12.5 if number == 1 else -8 - number * 5
            api('/api/devices/' + device + '/command', {'command': 'set_gain', 'params': {'channel': 1, 'gain': gain}})
            after = until(lambda: (state if (state := physical(name))['gain1'] == gain else None))
            assert after['writes'] == before['writes'] + 1
            assert after['wrong_threads'] == 0
            api('/api/devices/' + device + '/command', {'command': 'increment', 'params': {}})
            assert physical(name)['counter'] == before['counter'] + 1
            physical(name, 'external -7.5')
            until(lambda: api('/api/devices/' + device)['state'].get('channel.2.gain') == -7.5)
        recorded('Commands reach each hardware process once; independent hardware changes return to OpenAVC')
        api('/api/devices/ordinary/command', {'command': 'set_level', 'params': {'level': 42}})
        until(lambda: api('/api/devices/ordinary')['state'].get('level') == 42)
        recorded('The same server controls ordinary non-SDK equipment')
        if layout.get('faults'):
            before = physical('product2')
            assert equipment_probe('--wrong-trust') == {'tls_refused': True}
            assert equipment_probe('--wrong-login')['status'] == 401
            assert physical('product2')['writes'] == before['writes']
            recorded('Untrusted TLS and incorrect credentials refuse access without hardware work')
            description = equipment_probe()['response']
            invalid = {'session': description['session'], 'id': 1, 'command': 'increment', 'params': {'undeclared': 1}}
            refused = equipment_probe('--path', '/v1/command', '--body', json.dumps(invalid))
            assert refused['status'] == 422 and refused['response']['error'] == 'invalid_parameter'
            invalid.update(session='expired-example-session', params={})
            refused = equipment_probe('--path', '/v1/command', '--body', json.dumps(invalid))
            assert refused['status'] == 409 and refused['response']['error'] == 'session_changed'
            assert physical('product2')['writes'] == before['writes']
            recorded('Invalid parameters and stale sessions do not reach the hardware')
            if layout['arrangement'] == 'embedded':
                host_before = physical('product1')
                assert equipment_probe('--host', 'product1')['status'] == 403
                assert physical('product1')['writes'] == host_before['writes']
                recorded('The active host refuses remote equipment access through its remembered address')
            product_status('product2', 'remote_access', False)
            try:
                assert equipment_probe()['status'] == 403
                until(lambda: not api('/api/devices/product2')['connected'], timeout=60)
                api('/api/devices/product2/command', {'command': 'increment', 'params': {}}, expected=503)
                assert physical('product2')['writes'] == before['writes']
            finally:
                product_status('product2', 'remote_access', True)
            until(lambda: api('/api/devices/product2')['connected'], timeout=90)
            recorded('Disabled remote access refuses control and re-enabling it restores the normal connection')
            physical('product2', 'readfail 1')
            try:
                until(lambda: equipment_probe('--path', '/v1/snapshot')['status'] == 503)
                until(lambda: not api('/api/devices/product2')['connected'], timeout=60)
                assert api('/api/devices/product2')['state']['channel.1.gain'] is None
                assert physical('product2')['writes'] == before['writes']
            finally:
                physical('product2', 'readfail 0')
            until(lambda: api('/api/devices/product2')['connected'], timeout=90)
            until(lambda: api('/api/devices/product2')['state'].get('channel.1.gain') == before['gain1'])
            recorded('Unavailable readback clears stale values; current feedback returns after hardware recovery')
            physical('product2', 'refuse 1')
            try:
                refused = api('/api/devices/product2/command', {'command': 'increment', 'params': {}}, expected=409)
                assert refused['outcome'] == 'rejected'
                assert physical('product2')['writes'] == before['writes']
            finally:
                physical('product2', 'refuse 0')
            api('/api/devices/product2/command', {'command': 'increment', 'params': {}})
            assert physical('product2')['counter'] == before['counter'] + 1
            recorded('A hardware admission refusal is reported as rejected; a later new command runs once')
        if layout.get('recovery'):
            peer = 'product2'
            container = layout['products'][peer]['container']
            initial = physical(peer)
            docker('stop', '--time', '20', container)
            until(lambda: not api('/api/devices/' + peer)['connected'], timeout=60)
            offline = api('/api/devices/' + peer)
            assert offline['state'].get('offline_reason'), offline
            api('/api/devices/' + peer + '/command', {'command': 'increment', 'params': {}}, expected=503)
            for name, device in devices.items():
                if name == peer:
                    continue
                before = physical(name)
                api('/api/devices/' + device + '/command', {'command': 'increment', 'params': {}})
                assert physical(name)['counter'] == before['counter'] + 1
            api('/api/devices/ordinary/command', {'command': 'set_level', 'params': {'level': 57}})
            until(lambda: api('/api/devices/ordinary')['state'].get('level') == 57)
            recorded('An offline peer reports unavailable control while all other equipment remains controllable')
            started = time.monotonic()
            docker('start', container)
            until(lambda: api('/api/devices/' + peer)['connected'], timeout=90)
            restored = physical(peer)
            # The invented hardware resets its counter on process start. Neither
            # the old completed command nor the refused offline command may replay.
            assert restored['counter'] == 0 and restored['writes'] == 0, restored
            api('/api/devices/' + peer + '/command', {'command': 'increment', 'params': {}})
            current = physical(peer)
            assert current['counter'] == 1 and current['wrong_threads'] == 0, current
            status = product_status(peer)
            assert not status['server'] and not status['server_running']
            report['observations']['peer_restart'] = {
                'offline_reason': offline['state']['offline_reason'],
                'reconnect_seconds': round(time.monotonic() - started, 3),
                'before': initial, 'after_reconnect': restored, 'after_new_command': current}
            recorded('The peer reconnects without replaying old or refused commands; its server remains stopped')
            before_stop = {name: physical(name) for name in devices}
            if layout['arrangement'] == 'embedded':
                product_status('product1', 'server', False)
            else:
                docker('stop', '--time', '20', layout['controller'])

            def controller_available():
                try:
                    with opener.open(layout['server_url'] + '/api/health', timeout=2) as response:
                        return response.status == 200
                except (urllib.error.URLError, OSError):
                    return False

            until(lambda: not controller_available())
            for name in devices:
                status = product_status(name)
                assert not status['server_running'] and not status['server']
                physical(name, 'external -9.5')
                current = physical(name)
                assert current['counter'] == before_stop[name]['counter']
                assert current['gain2'] == -9.5
            recorded('Stopping the controller leaves all product hardware operating; no peer starts a server')
            if layout['arrangement'] == 'embedded':
                product_status('product1', 'server', True)
            else:
                docker('start', layout['controller'])
            until(controller_available, timeout=60)
            until(lambda: wanted <= {device['id'] for device in api('/api/devices')['devices'] if device['connected']}, timeout=90)
            for name, device in devices.items():
                until(lambda: api('/api/devices/' + device)['state'].get('channel.2.gain') == -9.5)
                assert physical(name)['counter'] == before_stop[name]['counter']
                api('/api/devices/' + device + '/command', {'command': 'increment', 'params': {}})
                assert physical(name)['counter'] == before_stop[name]['counter'] + 1
            report['observations']['controller_restart'] = {
                'hardware_before': before_stop,
                'hardware_after_new_commands': {name: physical(name) for name in devices},
                'administrator_login_retained': True}
            recorded('The selected controller restarts with its account and project, resynchronizes feedback and controls every product')
        if layout['arrangement'] == 'embedded':
            api('/api/devices/local', method='DELETE', expected=409)
            recorded('Product hardware cannot be removed')
        api('/api/discovery/scan', {'subnets': [value + '/32' for value in layout['addresses'].values()],
                                   'snmp_enabled': False, 'timeout': 35})
        until(lambda: api('/api/discovery/status')['status'] != 'running', timeout=90)
        discovered = api('/api/discovery/results')
        (folder / 'discovery.json').write_text(json.dumps(discovered, indent=2) + '\n')
        equipment = {device['ip'] for device in discovered['devices']
                     if (device.get('identification') or {}).get('driver_id') == 'openavc_equipment'}
        expected = {layout['addresses'][name] for name in devices
                    if name != 'product1' or layout['arrangement'] == 'external'}
        assert equipment == expected, (equipment, expected)
        recorded('Normal Discovery finds the remote equipment and excludes active built-in hardware')
        api('/api/devices/product2', method='DELETE')
        added = api('/api/discovery/add-device', {'ip': layout['addresses']['product2'],
            'driver_id': 'openavc_equipment', 'name': 'Discovered amplifier', 'config': login})
        new_id = added['device_id']
        until(lambda: api('/api/devices/' + new_id)['connected'])
        before = physical('product2')
        api('/api/devices/' + new_id + '/command', {'command': 'increment', 'params': {}})
        assert physical('product2')['counter'] == before['counter'] + 1
        recorded('Discovery Add uses normal credentials and controls the discovered product')
        report['status'] = 'passed'
        print('System example passed. Evidence: ' + str(folder / 'checks.json'), flush=True)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        (folder / 'checks.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    check(parser.parse_args().run.resolve())
