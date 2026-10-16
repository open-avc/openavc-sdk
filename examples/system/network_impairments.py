"""Measure kernel impairments and hardware effects on the same isolated link."""
import json
import statistics


def check_impairments(layout, report, *, api, docker, physical, until, recorded, **unused):
    peer = layout['products']['product2']['container']
    controller = layout['controller']
    network = layout['network']
    details = json.loads(docker('inspect', '-f', '{{json .NetworkSettings.Networks}}', controller))[network]
    address = details['GlobalIPv6Address' if layout.get('equipment_ipv6') else 'IPAddress']

    def tool(*args):
        return docker('run', '--rm', '--network', 'container:' + peer,
                      '--label', 'openavc.sdk.example=' + network, '--cap-drop', 'ALL',
                      '--cap-add', 'NET_ADMIN', '--security-opt', 'no-new-privileges',
                      layout['network_tools_image'], *args)

    route = json.loads(tool('ip', '-6' if layout.get('equipment_ipv6') else '-4', '-j', 'route', 'get', address))[0]
    interface = route['dev']
    policies = {'latency': ['delay', '80ms'], 'duplication': ['duplicate', '100%'],
                'reordering': ['delay', '100ms', 'reorder', '50%', 'gap', '5']}
    if layout.get('equipment_ipv6'):
        policies['loss'] = ['loss', '10%']
    report['observations']['impairments'] = {}
    for index, (name, policy) in enumerate(policies.items()):
        helper = network + '-datagrams'
        created = applied = False
        before = physical('product2')
        observation = {'interface': interface, 'receiver': address, 'policy': policy, 'before': before}
        report['observations']['impairments'][name] = observation
        try:
            docker('run', '-d', '--name', helper, '--network', 'container:' + controller,
                   '--label', 'openavc.sdk.example=' + network, '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', layout['image'],
                   'examples/system/datagrams.py', 'receive', address, '29020')
            created = True
            until(lambda: 'READY' in docker('logs', helper), timeout=10)
            tool('tc', 'qdisc', 'add', 'dev', interface, 'root', 'handle', '10:', 'netem', *policy)
            applied = True
            observation['configured_qdisc'] = json.loads(tool('tc', '-s', '-j', 'qdisc', 'show', 'dev', interface))
            docker('exec', peer, 'python', 'examples/system/datagrams.py', 'send', address, '29020')
            # UDP exposes the impairment directly. OpenAVC still uses its normal
            # TLS/TCP connection over this interface; no alternate command path.
            if name != 'loss':
                api('/api/devices/product2/command', {'command': 'increment', 'params': {}})
            gain = -6.5 - index
            physical('product2', 'external ' + str(gain))
            until(lambda: api('/api/devices/product2')['state'].get('channel.2.gain') == gain, timeout=30)
            api('/api/devices/ordinary/command', {'command': 'set_level', 'params': {'level': 65}})
            until(lambda: api('/api/devices/ordinary')['state'].get('level') == 65)
            assert docker('wait', helper) == '0'
            measurement = json.loads(docker('logs', helper).splitlines()[-1])
            observation['datagrams'] = measurement
            if name == 'loss':
                assert 0 < measurement['unique_received'] < measurement['sent'], measurement
            else:
                assert measurement['unique_received'] == measurement['sent'], measurement
            if name == 'latency':
                assert statistics.median(item['delay_ms'] for item in measurement['samples']) >= 70
            elif name == 'duplication':
                assert measurement['duplicates'] > 0
            elif name == 'reordering':
                assert measurement['out_of_order'] > 0
            qdisc = json.loads(tool('tc', '-s', '-j', 'qdisc', 'show', 'dev', interface))
            observation['qdisc'] = qdisc
            assert any(item['kind'] == 'netem' and item['packets'] > 0 for item in qdisc), qdisc
        except BaseException as error:
            observation['error'] = str(error)
            if created:
                observation['receiver_log'] = docker('logs', helper)
            raise
        finally:
            if applied:
                tool('tc', 'qdisc', 'del', 'dev', interface, 'root', 'handle', '10:')
            if created:
                docker('rm', '-f', helper)
        until(lambda: api('/api/devices/product2')['connected'], timeout=90)
        until(lambda: api('/api/devices/product2')['state'].get('channel.2.gain') == gain)
        after = physical('product2')
        commands = 0 if name == 'loss' else 1
        assert after['counter'] == before['counter'] + commands and after['writes'] == before['writes'] + commands
        assert after['callbacks'] == before['callbacks'] + commands and after['wrong_threads'] == 0
        api('/api/devices/product2/command', {'command': 'increment', 'params': {}})
        final = physical('product2')
        assert final['counter'] == after['counter'] + 1 and final['writes'] == after['writes'] + 1
        assert final['callbacks'] == after['callbacks'] + 1 and final['wrong_threads'] == 0
        observation.update(commands_during_impairment=commands, after=after, fresh_command=final)
        recorded('Measured ' + name + ' preserves command counts, feedback and unrelated TCP control')
