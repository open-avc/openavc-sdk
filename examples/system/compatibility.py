"""Exercise compiled model updates without changing controller programming."""
import hashlib
import json
import subprocess
import urllib.error


def check_compatibility(layout, report, *, folder, peer_device, api, docker,
                        physical, until, recorded):
    peer = layout['products']['product2']['container']
    variants = folder / 'model-variants'
    with (folder / 'model-variants-build.log').open('w') as log:
        subprocess.run(layout['docker'] + ['exec', peer, 'python',
            'examples/system/model_variants.py', '--out', '/tmp/model-variants'],
            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    docker('cp', peer + ':/tmp/model-variants', str(variants))
    observations = report['observations']['compatibility'] = []

    def project_hash():
        return hashlib.sha256(json.dumps(api('/api/project'), sort_keys=True).encode()).hexdigest()

    def effects(state, expected):
        assert state['wrong_threads'] == 0
        assert all(state[key] == expected for key in ('writes', 'callbacks', 'counter')), state

    targets = [('product2', peer_device)]
    if layout['arrangement'] == 'embedded':
        targets.append(('product1', 'local'))
    for product, device in targets:
        container = layout['products'][product]['container']
        labels = json.loads(docker('inspect', '-f', '{{json .Config.Labels}}', container))
        assert labels['openavc.sdk.example'] == layout['network']
        path = '/api/devices/' + device
        before = api(path)
        identity = before['state']['_sdk_identity']
        original_project = project_hash()
        for variant in ('additive', 'removed'):
            source = variants / variant
            manifest = json.loads((source / 'manifest.json').read_text())
            for name, digest in manifest['files'].items():
                assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest
            item = {'product': product, 'device': device, 'variant': variant,
                    'manifest': manifest, 'identity': identity, 'project_before': original_project}
            observations.append(item)
            docker('stop', '--time', '20', container)
            if product == 'product2':
                until(lambda: not api(path)['connected'], timeout=60)
                api('/api/devices/ordinary/command', {'command': 'set_level', 'params': {'level': 73}})
                assert api('/api/devices/ordinary')['state']['level'] == 73
            for name, target in [('build/example-evolution', '/opt/sdk/build/example-c'),
                                 ('build/sdk/openavc-device-service', '/opt/sdk/build/openavc-device-service')]:
                docker('cp', str(source / name), container + ':' + target)
            docker('cp', str(source / 'build/sdk/generated') + '/.', container + ':/opt/sdk/build/generated/')
            docker('start', container)

            def connected():
                try:
                    current = api(path)
                except (OSError, urllib.error.URLError, AssertionError):
                    return None
                return current if current['connected'] and current['driver_info']['version'] == manifest['version'] else None

            current = until(connected, timeout=90)
            assert current['state']['_sdk_identity'] == identity
            assert 'advance' in current['commands']
            assert ('set_gain' in current['commands']) == (variant == 'additive')
            item.update(project_after=project_hash(), commands=sorted(current['commands']),
                        version=current['driver_info']['version'], hardware_before=physical(product))
            assert item['project_after'] == original_project
            effects(item['hardware_before'], 0)
            api(path + '/command', {'command': 'advance', 'params': {}})
            item['hardware_after'] = physical(product)
            effects(item['hardware_after'], 1)
            if variant == 'removed':
                item['removed_command'] = api(path + '/command', {
                    'command': 'set_gain', 'params': {'channel': 1, 'gain': -12}}, expected=404)
                assert 'set_gain' in item['removed_command']['detail']
                effects(physical(product), 1)
            item['status'] = 'passed'
            recorded(f'{product} {variant} model reconnects with unchanged programming and one hardware effect')
