"""Prepare the example project using normal device and panel definitions."""
from copy import deepcopy


def project(addresses, credentials, *, embedded):
    devices = []
    products = [name for name in addresses if name.startswith('product')]
    for number in range(1, len(products) + 1):
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
    controls = []
    for number, name in enumerate(products, 1):
        gain = -12.5 if number == 1 else -8 - number * 5
        controls.append((local if number == 1 else name, f'Amplifier {number} gain: {gain:g} dB',
                         'set_gain', {'channel': 1, 'gain': gain}, 'channel.1.gain'))
    controls.append(('ordinary', 'TCP equipment level: 42', 'set_level', {'level': 42}, 'level'))
    row_height = 90 / len(controls)
    for row, (device, label, command, params, state) in enumerate(controls):
        control, reading = f'control{row}', f'reading{row}'
        elements.extend([
            {'id': control, 'type': 'button', 'label': label, 'bindings': {'do': {'press': [
                {'action': 'device.command', 'device': device, 'command': command, 'params': params}]}}},
            {'id': reading, 'type': 'label', 'text': 'Readback', 'bindings': {'show': {'value': {'key': f'device.{device}.{state}'}}}},
        ])
        placements[control] = {'x': 5, 'y': 5 + row * row_height, 'w': 55, 'h': row_height * 2 / 3}
        placements[reading] = {'x': 65, 'y': 5 + row * row_height, 'w': 30, 'h': row_height * 2 / 3}
    return {'openavc_version': '0.14.0', 'project': {'id': 'sdk_example', 'name': 'Manufacturer SDK example'},
        'devices': devices, 'ui': {'pages': [{'id': 'main', 'name': 'Equipment controls', 'page_type': 'page',
            'elements': elements, 'layouts': [{'id': 'landscape', 'orientation': 'landscape', 'primary': True,
                                            'placements': deepcopy(placements)}]}]}}
