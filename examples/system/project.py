"""Prepare the example project using normal device and panel definitions."""
from copy import deepcopy


def project(addresses, credentials, *, embedded):
    devices = []
    for number in (1, 2):
        if embedded and number == 1:
            continue
        devices.append({'id': f'product{number}', 'name': f'Example amplifier {number}', 'driver': 'openavc_equipment',
            'config': {'host': addresses[f'product{number}'], 'port': 9443, **credentials}})
    devices.append({'id': 'ordinary', 'name': 'Ordinary TCP equipment', 'driver': 'generic_tcp',
        'config': {'host': addresses['ordinary'], 'port': 19000, 'delimiter': '\r\n', 'poll_interval': 2,
            'commands': {'read_level': {'label': 'Read Level', 'send': 'POLL', 'poll': True},
                'set_level': {'label': 'Set Level', 'send': 'LEVEL {level}',
                'params': {'level': {'type': 'integer', 'min': 0, 'max': 100, 'required': True}}}},
            'responses': [{'mode': 'prefix_number', 'prefix': 'LEVEL=', 'state': 'level', 'type': 'integer'}]}})
    local = 'local' if embedded else 'product1'
    elements, placements = [], {}
    for row, (device, label, command, params, state) in enumerate([
        (local, 'Amplifier 1 gain: -12.5 dB', 'set_gain', {'channel': 1, 'gain': -12.5}, 'channel.1.gain'),
        ('product2', 'Amplifier 2 gain: -18 dB', 'set_gain', {'channel': 1, 'gain': -18}, 'channel.1.gain'),
        ('ordinary', 'TCP equipment level: 42', 'set_level', {'level': 42}, 'level'),
    ]):
        control, reading = f'control{row}', f'reading{row}'
        elements.extend([
            {'id': control, 'type': 'button', 'label': label, 'bindings': {'do': {'press': [
                {'action': 'device.command', 'device': device, 'command': command, 'params': params}]}}},
            {'id': reading, 'type': 'label', 'text': 'Readback', 'bindings': {'show': {'value': {'key': f'device.{device}.{state}'}}}},
        ])
        placements[control] = {'x': 5, 'y': 5 + row * 30, 'w': 55, 'h': 20}
        placements[reading] = {'x': 65, 'y': 5 + row * 30, 'w': 30, 'h': 20}
    return {'openavc_version': '0.14.0', 'project': {'id': 'sdk_example', 'name': 'Manufacturer SDK example'},
        'devices': devices, 'ui': {'pages': [{'id': 'main', 'name': 'Equipment controls', 'page_type': 'page',
            'elements': elements, 'layouts': [{'id': 'landscape', 'orientation': 'landscape', 'primary': True,
                                            'placements': deepcopy(placements)}]}]}}
