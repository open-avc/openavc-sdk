"""Exercise ordinary device setup across two isolated, routed subnets."""
import base64
import json
import re


def check_routes(layout, report, *, api, docker, physical, until, recorded, **unused):
    network = layout['network']
    second = network + '-routed'
    router = network + '-router'
    peer = layout['products']['product2']['container']
    controller = layout['controller']
    ipv6 = layout.get('equipment_ipv6', False)
    family, ip_key = ('-6', 'GlobalIPv6Address') if ipv6 else ('-4', 'IPAddress')
    firewall = 'ip6tables' if ipv6 else 'iptables'

    def endpoints(container):
        return json.loads(docker('inspect', '-f', '{{json .NetworkSettings.Networks}}', container))

    def tool(container, *args):
        return docker('run', '--rm', '--network', 'container:' + container,
                      '--label', 'openavc.sdk.example=' + network, '--cap-drop', 'ALL',
                      '--cap-add', 'NET_ADMIN', '--security-opt', 'no-new-privileges',
                      layout['network_tools_image'], *args)

    def subnet(name):
        configs = json.loads(docker('network', 'inspect', name))[0]['IPAM']['Config']
        return next(item['Subnet'] for item in configs if (':' in item['Subnet']) == ipv6)

    def count(container, chain, target):
        rules = tool(container, firewall + '-save', '-c')
        values = re.findall(r'\[(\d+):(\d+)\] -A ' + chain + r' [^\n]* -j ' + target, rules)
        assert len(values) == 1, rules
        return {'packets': int(values[0][0]), 'bytes': int(values[0][1])}

    original = endpoints(peer)[network]
    main = endpoints(controller)[network]
    identity = api('/api/devices/product2')['state']['_sdk_identity']
    before = physical('product2')
    process = docker('inspect', '-f', '{{.State.Pid}}', peer)
    hashes = "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('/data/server.pem').read_bytes()).hexdigest())"
    certificate = docker('exec', peer, 'python', '-c', hashes)
    reach = api('/api/system/config')['network']['reach_devices_through']
    original_hosts = docker('exec', controller, 'python', '-c',
                           "import base64,pathlib; print(base64.b64encode(pathlib.Path('/etc/hosts').read_bytes()).decode())")
    created = router_created = controller_attached = peer_attached = peer_moved = False
    paused = manual_added = reach_changed = hosts_changed = False
    routes = []
    try:
        docker('network', 'create', '--internal', *(['--ipv6'] if ipv6 else []),
               '--label', 'openavc.sdk.example=' + network, second)
        created = True
        docker('run', '-d', '--name', router, '--network', network,
               '--label', 'openavc.sdk.example=' + network, '--cap-drop', 'ALL',
               '--cap-add', 'NET_ADMIN', '--security-opt', 'no-new-privileges',
               '--sysctl', 'net.ipv4.ip_forward=1',
               *(['--sysctl', 'net.ipv6.conf.all.forwarding=1'] if ipv6 else []),
               layout['network_tools_image'], 'sleep', '1800')
        router_created = True
        docker('network', 'connect', second, router)
        docker('network', 'connect', second, controller)
        controller_attached = True
        route = json.loads(tool(controller, 'ip', family, '-j', 'route', 'get', original[ip_key]))[0]
        interface = route['dev']
        multi = {'controller_networks': list(endpoints(controller)), 'route': route,
                 'address_family': 'IPv6' if ipv6 else 'IPv4'}
        if not ipv6:
            # Exercise the same adapter identity Settings > Network saves.
            api('/api/system/config', {'network': {'reach_devices_through': {
                'name': interface, 'mac': main['MacAddress']}}}, method='PATCH')
            reach_changed = True
        else:
            multi['adapter_selection'] = 'not tested: the pinned core adapter inventory selects an IPv4 source'
        rule = ('INPUT', '-p', 'tcp', '-s', main[ip_key], '--dport', '9443', '-j', 'ACCEPT')
        tool(peer, firewall, '-I', *rule)
        try:
            api('/api/devices/product2/pause', {}, method='POST')
            paused = True
            api('/api/devices/product2/resume', {}, method='POST')
            paused = False
            until(lambda: api('/api/devices/product2')['connected'], timeout=90)
            api('/api/devices/product2/command', {'command': 'increment', 'params': {}})
            multi['source_packets'] = count(peer, 'INPUT', 'ACCEPT')
            assert multi['source_packets']['packets'] > 0
            assert physical('product2')['counter'] == before['counter'] + 1
        finally:
            tool(peer, firewall, '-D', *rule)
        report['observations']['multiple_interfaces'] = multi
        recorded('Equipment control uses the intended source with two controller network interfaces')
        docker('network', 'disconnect', second, controller)
        controller_attached = False
        docker('network', 'connect', second, peer)
        peer_attached = True
        remote = endpoints(peer)[second][ip_key]
        routing = endpoints(router)
        docker('network', 'disconnect', network, peer)
        peer_moved = True
        for container, destination, gateway in (
                (controller, subnet(second), routing[network][ip_key]),
                (peer, subnet(network), routing[second][ip_key])):
            tool(container, 'ip', family, 'route', 'add', destination, 'via', gateway)
            routes.append((container, destination, gateway))
        until(lambda: not api('/api/devices/product2')['connected'], timeout=90)
        offline = api('/api/devices/product2')
        assert offline['state'].get('offline_reason') and offline['state'].get('channel.1.gain') is None
        # A saved numeric address does not follow an address change. The
        # temporary hostname below is controlled fixture DNS, not a DHCP claim.
        api('/api/devices/product2/pause', {}, method='POST')
        paused = True
        host_data = base64.b64decode(original_hosts).decode() + '\n' + remote + ' product2\n'
        encoded = base64.b64encode(host_data.encode()).decode()
        docker('exec', controller, 'python', '-c',
               "import base64,pathlib; pathlib.Path('/etc/hosts').write_bytes(base64.b64decode(" + repr(encoded) + '))')
        hosts_changed = True
        forward = ('FORWARD', '-p', 'tcp', '--dport', '9443', '-d', remote, '-j', 'ACCEPT')
        tool(router, firewall, '-I', *forward)
        project, headers = api('/api/project', include_headers=True)
        login = json.loads(docker('exec', peer, 'python', '-c',
                                 "import pathlib; print(pathlib.Path('/data/login.json').read_text())"))
        project['devices'].append({'id': 'routed_check', 'name': 'Routed equipment',
                                   'driver': 'openavc_equipment', 'config': login, 'enabled': True})
        project['connections']['routed_check'] = {'host': 'product2', 'port': 9443}
        api('/api/project', project, method='PUT', headers={'If-Match': headers['ETag']})
        manual_added = True
        until(lambda: api('/api/devices/routed_check')['connected'], timeout=90)
        assert api('/api/devices/routed_check')['state']['_sdk_identity'] == identity
        api('/api/devices/routed_check/command', {'command': 'increment', 'params': {}})
        routed = physical('product2')
        assert routed['counter'] == before['counter'] + 2 and routed['writes'] == before['writes'] + 2
        forwarded = count(router, 'FORWARD', 'ACCEPT')
        assert forwarded['packets'] > 0
        drop = ('FORWARD', '-p', 'tcp', '--dport', '9443', '-d', remote, '-j', 'DROP')
        tool(router, firewall, '-I', *drop)
        try:
            until(lambda: not api('/api/devices/routed_check')['connected'], timeout=90)
            unavailable = api('/api/devices/routed_check')
            assert unavailable['state'].get('offline_reason') and unavailable['state'].get('channel.1.gain') is None
            api('/api/devices/routed_check/command', {'command': 'increment', 'params': {}}, expected=503)
            physical('product2', 'external -3.5')
            other_id = 'local' if layout['arrangement'] == 'embedded' else 'product1'
            other_before = physical('product1')
            api('/api/devices/' + other_id + '/command', {'command': 'increment', 'params': {}})
            assert physical('product1')['counter'] == other_before['counter'] + 1
            api('/api/devices/ordinary/command', {'command': 'set_level', 'params': {'level': 66}})
            until(lambda: api('/api/devices/ordinary')['state'].get('level') == 66)
            dropped = count(router, 'FORWARD', 'DROP')
            assert dropped['packets'] > 0 and physical('product2')['writes'] == routed['writes']
        finally:
            tool(router, firewall, '-D', *drop)
        until(lambda: api('/api/devices/routed_check')['connected'], timeout=90)
        until(lambda: api('/api/devices/routed_check')['state'].get('channel.2.gain') == -3.5)
        assert physical('product2')['writes'] == routed['writes']
        api('/api/devices/routed_check/command', {'command': 'increment', 'params': {}})
        after = physical('product2')
        assert after['counter'] == routed['counter'] + 1 and after['writes'] == routed['writes'] + 1
        assert after['callbacks'] == before['callbacks'] + 3 and after['wrong_threads'] == 0
        assert docker('inspect', '-f', '{{.State.Pid}}', peer) == process
        assert docker('exec', peer, 'python', '-c', hashes) == certificate
        report['observations']['routed_subnets'] = {
            'original_address': original[ip_key], 'new_address': remote, 'identity': identity,
            'controller_networks': list(endpoints(controller)), 'peer_networks': list(endpoints(peer)),
            'router_networks': list(endpoints(router)), 'forwarded': forwarded, 'dropped': dropped,
            'routes': [{'container': item[0], 'subnet': item[1], 'gateway': item[2]} for item in routes],
            'before': before, 'after_routed_command': routed, 'after_recovery_command': after,
            'tls_verified': True, 'certificate_sha256': certificate, 'process_unchanged': True,
            'numeric_address_offline_reason': offline['state']['offline_reason'],
            'routed_offline_reason': unavailable['state']['offline_reason']}
        recorded('Manual setup, truthful outage and recovery work across routed subnets without command replay')
    except BaseException as error:
        diagnostics = {'error': str(error)}
        report['observations']['routing_failure'] = diagnostics
        for container in (controller, peer, router):
            try:
                diagnostics[container] = {'networks': endpoints(container),
                    'routes': tool(container, 'ip', family, '-j', 'route'),
                    'firewall': tool(container, firewall + '-save', '-c')}
            except Exception as diagnostic_error:
                diagnostics[container] = {'diagnostic_error': str(diagnostic_error)}
        raise
    finally:
        if manual_added:
            api('/api/devices/routed_check', method='DELETE')
        if hosts_changed:
            docker('exec', controller, 'python', '-c',
                   "import base64,pathlib; pathlib.Path('/etc/hosts').write_bytes(base64.b64decode(" + repr(original_hosts) + '))')
        for container, destination, gateway in reversed(routes):
            tool(container, 'ip', family, 'route', 'del', destination, 'via', gateway)
        if peer_moved:
            args = ['network', 'connect', '--ip', original['IPAddress']]
            if ipv6:
                args += ['--ip6', original['GlobalIPv6Address']]
            for alias in original.get('Aliases') or []:
                args += ['--alias', alias]
            docker(*args, network, peer)
        if peer_attached:
            docker('network', 'disconnect', second, peer)
        if controller_attached:
            docker('network', 'disconnect', second, controller)
        if router_created:
            docker('rm', '-f', router)
        if created:
            docker('network', 'rm', second)
        if reach_changed:
            api('/api/system/config', {'network': {'reach_devices_through': reach}}, method='PATCH')
        if paused:
            api('/api/devices/product2/resume', {}, method='POST')
    until(lambda: api('/api/devices/product2')['connected'], timeout=90)
