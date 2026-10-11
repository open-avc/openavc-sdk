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
    report = {'status': 'running', 'arrangement': layout['arrangement'], 'checks': []}

    def api(path, data=None, method=None, expected=200):
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

    def recorded(name):
        report['checks'].append(name)
        (folder / 'checks.json').write_text(json.dumps(report, indent=2) + '\n')

    try:
        assert api('/api/auth/required')['state'] == 'setup'
        api('/api/auth/setup', account)
        local = 'local' if layout['arrangement'] == 'embedded' else 'product1'
        wanted = {local, 'product2', 'ordinary'}
        until(lambda: wanted <= {device['id'] for device in api('/api/devices')['devices'] if device['connected']})
        recorded('Both SDK products and ordinary TCP equipment connect')
        for number, device in ((1, local), (2, 'product2')):
            name = f'product{number}'
            before = physical(name)
            gain = -12.5 if number == 1 else -18
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
        if layout['arrangement'] == 'embedded':
            api('/api/devices/local', method='DELETE', expected=409)
            recorded('Built-in equipment cannot be removed')
        api('/api/discovery/scan', {'subnets': [value + '/32' for value in layout['addresses'].values()],
                                   'snmp_enabled': False, 'timeout': 35})
        until(lambda: api('/api/discovery/status')['status'] != 'running', timeout=90)
        discovered = api('/api/discovery/results')
        (folder / 'discovery.json').write_text(json.dumps(discovered, indent=2) + '\n')
        equipment = {device['ip'] for device in discovered['devices']
                     if (device.get('identification') or {}).get('driver_id') == 'openavc_equipment'}
        expected = {layout['addresses']['product2']}
        if layout['arrangement'] == 'external':
            expected.add(layout['addresses']['product1'])
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
