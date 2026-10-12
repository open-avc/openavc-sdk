"""Network faults confined to one invented product's container namespace."""
import json
import re
import time


def check_network_faults(layout, report, *, api, docker, physical, until, recorded, product_status):
    peer = layout['products']['product2']['container']
    labels = json.loads(docker('inspect', '-f', '{{json .Config.Labels}}', peer))
    if labels.get('openavc.sdk.example') != layout['network']:
        raise ValueError('Network faults require a product owned by this example.')
    image = layout['network_tools_image']

    def tool(*args):
        return docker('run', '--rm', '--network', 'container:' + peer,
                      '--label', 'openavc.sdk.example=' + layout['network'],
                      '--cap-drop', 'ALL', '--cap-add', 'NET_ADMIN',
                      '--security-opt', 'no-new-privileges', image, *args)

    def dropped(chain):
        rules = tool('iptables-save', '-c')
        matches = re.findall(r'\[(\d+):(\d+)\] -A ' + chain + r' [^\n]* -j DROP', rules)
        if len(matches) != 1:
            raise AssertionError('The expected isolated DROP rule was not found.')
        return {'packets': int(matches[0][0]), 'bytes': int(matches[0][1])}

    assert product_status('product2')['equipment']['discovery_active']
    original_identity = api('/api/devices/product2')['state']['_sdk_identity']
    applied, manual_added, paused = [], False, False
    multicast_rules = [('INPUT', '-p', 'udp', '--dport', '5353', '-j', 'DROP'),
                       ('OUTPUT', '-p', 'udp', '--sport', '5353', '-j', 'DROP')]
    try:
        tool('iptables', '-I', *multicast_rules[0])
        applied.append(multicast_rules[0])
        api('/api/devices/product2/pause', {}, method='POST')
        paused = True
        until(lambda: not api('/api/devices/product2')['connected'])
        project, headers = api('/api/project', include_headers=True)
        login = json.loads(docker('exec', peer, 'python', '-c',
                                 "import pathlib; print(pathlib.Path('/data/login.json').read_text())"))
        project['devices'].append({'id': 'manual_network_check', 'name': 'Manual network check',
                                   'driver': 'openavc_equipment', 'config': login, 'enabled': True})
        project['connections']['manual_network_check'] = {'host': layout['addresses']['product2'], 'port': 9443}
        api('/api/project', project, method='PUT', headers={'If-Match': headers['ETag']})
        manual_added = True
        until(lambda: api('/api/devices/manual_network_check')['connected'], timeout=90)
        assert api('/api/devices/manual_network_check')['state']['_sdk_identity'] == original_identity
        # The manual connection above was made without a discovery result.
        # This scan now supplies actual multicast queries to the DROP rule.
        api('/api/discovery/scan', {'subnets': [layout['addresses']['product2'] + '/32'],
                                   'snmp_enabled': False, 'timeout': 35})
        until(lambda: api('/api/discovery/status')['status'] != 'running', timeout=90)
        assert dropped('INPUT')['packets'] > 0
        # Prove ingress filtering before denying sends: a send error closes
        # the advertiser socket and leaves its multicast group, so the kernel
        # may then discard ingress before it reaches the INPUT chain.
        tool('iptables', '-I', *multicast_rules[1])
        applied.append(multicast_rules[1])
        deadline = time.monotonic() + 15
        while not dropped('OUTPUT')['packets'] and time.monotonic() < deadline:
            time.sleep(1)
        counters = {chain: dropped(chain) for chain in ('INPUT', 'OUTPUT')}
        assert counters['INPUT']['packets'] > 0 and counters['OUTPUT']['packets'] > 0
        disabled = product_status('product2', 'discovery', False)
        assert not disabled['discovery'] and not disabled['equipment']['discovery_active']
        assert disabled['error'] is None
        enabled = product_status('product2', 'discovery', True)
        assert enabled['discovery'] and enabled['error'] is None
        before = physical('product2')
        api('/api/devices/manual_network_check/command', {'command': 'increment', 'params': {}})
        assert physical('product2')['counter'] == before['counter'] + 1
        report['observations']['blocked_multicast'] = {'dropped': counters,
            'manual_connection_identity': original_identity, 'tls_verification_enabled': True,
            'discovery_result_used_for_manual_add': False,
            'discovery_switches_work_while_filtered': True}
        recorded('Manual equipment setup and control work while kernel rules block product multicast discovery')
    finally:
        if manual_added:
            api('/api/devices/manual_network_check', method='DELETE')
        for rule in reversed(applied):
            tool('iptables', '-D', *rule)
        if paused:
            api('/api/devices/product2/resume', {}, method='POST')
    until(lambda: api('/api/devices/product2')['connected'], timeout=90)
    until(lambda: product_status('product2')['equipment']['discovery_active'], timeout=15)
    api('/api/discovery/scan', {'subnets': [layout['addresses']['product2'] + '/32'],
                               'snmp_enabled': False, 'timeout': 35})
    until(lambda: api('/api/discovery/status')['status'] != 'running', timeout=90)
    recovered = next(item for item in api('/api/discovery/results')['devices']
                     if item['ip'] == layout['addresses']['product2'])
    assert (recovered.get('identification') or {}).get('driver_id') == 'openavc_equipment'
    assert any(item['source'] == 'mdns:_openavc-device._tcp.local.' for item in recovered['evidence_log'])
    report['observations']['blocked_multicast']['discovery_recovers_without_restart'] = True
    recorded('Multicast discovery resumes after filtering is removed, without restarting the product')

    before = physical('product2')
    rule = ('OUTPUT', '-p', 'tcp', '--sport', '9443', '-m', 'statistic',
            '--mode', 'nth', '--every', '5', '--packet', '0', '-j', 'DROP')
    tool('iptables', '-I', *rule)
    samples = []
    started = time.monotonic()
    try:
        physical('product2', 'external -4.5')
        while time.monotonic() - started < 15:
            status = api('/api/devices/product2')
            samples.append({'elapsed_seconds': round(time.monotonic() - started, 3),
                            'connected': status['connected'],
                            'reported_gain': status['state'].get('channel.2.gain')})
        losses = dropped('OUTPUT')
        assert losses['packets'] > 0
        assert physical('product2')['writes'] == before['writes']
        api('/api/devices/ordinary/command', {'command': 'set_level', 'params': {'level': 64}})
        until(lambda: api('/api/devices/ordinary')['state'].get('level') == 64)
    finally:
        tool('iptables', '-D', *rule)
    recovered_at = time.monotonic()
    until(lambda: api('/api/devices/product2')['connected'], timeout=90)
    until(lambda: api('/api/devices/product2')['state'].get('channel.2.gain') == -4.5)
    after = physical('product2')
    assert after['counter'] == before['counter'] and after['writes'] == before['writes']
    api('/api/devices/product2/command', {'command': 'increment', 'params': {}})
    assert physical('product2')['counter'] == before['counter'] + 1
    report['observations']['packet_loss'] = {
        'policy': 'drop_every_fifth_outbound_tcp_packet_from_equipment_port',
        'minimum_observation_seconds': 15, 'dropped': losses, 'samples': samples,
        'recovery_check_seconds': round(time.monotonic() - recovered_at, 3),
        'before': before, 'after_recovery': after,
        'hardware_command_during_loss_tested': False}
    recorded('Feedback and control recover after observed packet loss, without replaying prior commands')
