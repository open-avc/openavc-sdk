"""Verify the running invented equipment using normal OpenAVC API operations."""
import argparse
import base64
import ipaddress
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
              'network_faults': layout.get('network_faults', False),
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

    def api(path, data=None, method=None, expected=200, *, headers=None, include_headers=False):
        nonlocal next_request
        # All these requests pass through the example's gateway. Pace the
        # observer below even the normal 60/minute API budget; the controller's
        # ordinary limits stay enabled. Never retry a hardware command.
        time.sleep(max(0.0, next_request - time.monotonic()))
        next_request = time.monotonic() + request_interval
        report['observations']['controller_requests']['count'] += 1
        request = urllib.request.Request(layout['server_url'] + path,
            data=json.dumps(data).encode() if data is not None else None, method=method,
            headers={'Content-Type': 'application/json', 'Authorization': authorization, **(headers or {})})
        try:
            response = opener.open(request, timeout=100)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            if response.status != expected:
                raise AssertionError(f'{path}: expected {expected}, received {response.status}: {raw.decode()}')
            body = json.loads(raw)
            return (body, response.headers) if include_headers else body

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
        if layout.get('authority_checks'):
            from authority import check_authority
            check_authority(layout, report, api=api, docker=docker, physical=physical,
                            until=until, recorded=recorded, credentials=login)
        if layout.get('resource_checks'):
            from resources import check_resources
            check_resources(layout, report, api=api, docker=docker, physical=physical,
                            until=until, recorded=recorded, product_status=product_status)
        if layout.get('network_faults'):
            from network_faults import check_network_faults
            context = dict(api=api, docker=docker, physical=physical, until=until,
                           recorded=recorded, product_status=product_status)
            if not layout.get('equipment_ipv6'):
                check_network_faults(layout, report, **context)
            else:
                for name in devices:
                    assert not product_status(name)['equipment']['discovery_active']
                report['observations']['ipv6_discovery'] = {'status': 'unsupported',
                    'reason': 'Equipment discovery uses IPv4; IPv6 connections use manual setup.'}
            from network_impairments import check_impairments
            from network_routes import check_routes
            check_impairments(layout, report, **context)
            check_routes(layout, report, **context)
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
            peer = layout['products']['product2']['container']
            peer_pid = docker('inspect', '-f', '{{.State.Pid}}', peer)
            endpoint = json.loads(docker('inspect', '-f', '{{json .NetworkSettings.Networks}}', peer))[layout['network']]
            before = physical('product2')
            docker('network', 'disconnect', layout['network'], peer)
            try:
                until(lambda: not api('/api/devices/product2')['connected'], timeout=90)
                offline = api('/api/devices/product2')
                assert offline['state'].get('offline_reason'), offline
                api('/api/devices/product2/command', {'command': 'increment', 'params': {}}, expected=503)
                physical('product2', 'external -6.5')
                isolated = physical('product2')
                assert isolated['counter'] == before['counter'] and isolated['writes'] == before['writes']
                assert isolated['gain2'] == -6.5
                for name, device in devices.items():
                    if name == 'product2':
                        continue
                    other = physical(name)
                    api('/api/devices/' + device + '/command', {'command': 'increment', 'params': {}})
                    assert physical(name)['counter'] == other['counter'] + 1
                api('/api/devices/ordinary/command', {'command': 'set_level', 'params': {'level': 63}})
                until(lambda: api('/api/devices/ordinary')['state'].get('level') == 63)
                recorded('An isolated peer keeps operating locally while the controller operates the other products')
            finally:
                reconnect_started = time.monotonic()
                arguments = ['network', 'connect', '--ip', endpoint['IPAddress']]
                for alias in endpoint.get('Aliases') or []:
                    arguments += ['--alias', alias]
                docker(*arguments, layout['network'], peer)
            assert docker('inspect', '-f', '{{.State.Pid}}', peer) == peer_pid
            until(lambda: api('/api/devices/product2')['connected'], timeout=90)
            until(lambda: api('/api/devices/product2')['state'].get('channel.2.gain') == -6.5)
            restored = physical('product2')
            assert restored['counter'] == before['counter'] and restored['writes'] == before['writes']
            api('/api/devices/product2/command', {'command': 'increment', 'params': {}})
            assert physical('product2')['counter'] == before['counter'] + 1
            assert not product_status('product2')['server_running']
            report['observations']['network_partition'] = {
                'address': endpoint['IPAddress'], 'offline_reason': offline['state']['offline_reason'],
                'reconnect_seconds': round(time.monotonic() - reconnect_started, 3),
                'container_process_unchanged': True, 'before': before,
                'isolated': isolated, 'after_reconnect': restored}
            recorded('Network recovery returns current feedback without restarting the peer or replaying commands')
            # A stable DNS name is already covered by the example certificate.
            # Move the actual endpoint while keeping that name, credentials,
            # hardware process and project bindings unchanged.
            original_connection = api('/api/connections')['connections'].get('product2')
            api('/api/connections/product2', {**(original_connection or {}), 'host': 'product2', 'port': 9443}, method='PUT')
            until(lambda: api('/api/devices/product2')['connected'], timeout=90)
            original_identity = api('/api/devices/product2')['state']['_sdk_identity']
            original_ui = api('/api/project')['ui']
            # Ask Docker's allocator; newer engines can omit the bridge's
            # reserved address from IPAM inspection on an internal network.
            # This isolated network has no other container creator during the
            # probe/reconnect interval. A collision still fails the exercise.
            changed_address = docker('run', '--rm', '--network', layout['network'],
                '--label', 'openavc.sdk.example=' + layout['network'], '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', layout['image'], '-c',
                "import socket; print(socket.gethostbyname(socket.gethostname()))")
            assert ipaddress.ip_address(changed_address) in ipaddress.ip_network(layout['network_subnet'])
            assert changed_address != endpoint['IPAddress']
            before = physical('product2')
            certificate_code = "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('/data/server.pem').read_bytes()).hexdigest())"
            certificate_hash = docker('exec', peer, 'python', '-c', certificate_code)

            def reconnect_at(address):
                arguments = ['network', 'connect', '--ip', address]
                for alias in sorted(set(endpoint.get('Aliases') or []) | {'product2'}):
                    arguments += ['--alias', alias]
                docker(*arguments, layout['network'], peer)

            docker('network', 'disconnect', layout['network'], peer)
            try:
                until(lambda: not api('/api/devices/product2')['connected'], timeout=90)
                api('/api/devices/product2/command', {'command': 'increment', 'params': {}}, expected=503)
                physical('product2', 'external -5.5')
                moved_at = time.monotonic()
                reconnect_at(changed_address)
                resolved = docker('exec', layout['controller'], 'python', '-c',
                                  "import socket; print(socket.gethostbyname('product2'))")
                assert resolved == changed_address and resolved != endpoint['IPAddress']
                until(lambda: api('/api/devices/product2')['connected'], timeout=90)
                changed_device = until(lambda: (item if (item := api('/api/devices/product2'))['state'].get('channel.2.gain') == -5.5 else None))
                assert changed_device['state']['_sdk_identity'] == original_identity
                assert api('/api/project')['ui'] == original_ui
                after = physical('product2')
                assert after['counter'] == before['counter'] and after['writes'] == before['writes']
                assert docker('inspect', '-f', '{{.State.Pid}}', peer) == peer_pid
                assert docker('exec', peer, 'python', '-c', certificate_code) == certificate_hash
                api('/api/devices/product2/command', {'command': 'increment', 'params': {}})
                assert physical('product2')['counter'] == before['counter'] + 1
                report['observations']['changed_address'] = {
                    'old_address': endpoint['IPAddress'], 'new_address': changed_address,
                    'configured_hostname': 'product2', 'identity': original_identity,
                    'reconnect_seconds': round(time.monotonic() - moved_at, 3),
                    'container_process_unchanged': True, 'certificate_unchanged': True,
                    'project_ui_unchanged': True, 'before': before, 'after_reconnect': after}
                recorded('A peer address change behind its configured hostname retains identity, bindings and verified TLS without command replay')
            finally:
                current = json.loads(docker('inspect', '-f', '{{json .NetworkSettings.Networks}}', peer))
                if current.get(layout['network'], {}).get('EndpointID'):
                    docker('network', 'disconnect', layout['network'], peer)
                reconnect_at(endpoint['IPAddress'])
                if original_connection is None:
                    api('/api/connections/product2', method='DELETE')
                else:
                    api('/api/connections/product2', original_connection, method='PUT')
            until(lambda: api('/api/devices/product2')['connected'], timeout=90)
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
            from controller_crash import check_controller_crash
            check_controller_crash(layout, report, api=api, docker=docker, physical=physical,
                                   until=until, recorded=recorded, product_status=product_status)
        if layout['arrangement'] == 'embedded':
            api('/api/devices/local', method='DELETE', expected=409)
            recorded('Product hardware cannot be removed')
        if layout.get('equipment_ipv6'):
            report['status'] = 'passed'
            print('IPv6 system example passed. Evidence: ' + str(folder / 'checks.json'), flush=True)
            return
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
        if layout.get('compatibility_checks'):
            from compatibility import check_compatibility
            check_compatibility(layout, report, folder=folder, peer_device=new_id, api=api,
                                docker=docker, physical=physical, until=until, recorded=recorded)
        report['status'] = 'passed'
        print('System example passed. Evidence: ' + str(folder / 'checks.json'), flush=True)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        try:
            report['observations']['failure_devices'] = [
                {'id': device['id'], 'connected': device['connected'],
                 'offline_reason': device.get('state', {}).get('offline_reason'),
                 'offline_detail': device.get('state', {}).get('offline_detail')}
                for device in api('/api/devices')['devices']]
        except Exception as diagnostic_error:
            report['observations']['failure_devices_error'] = str(diagnostic_error)
        raise
    finally:
        (folder / 'checks.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    check(parser.parse_args().run.resolve())
