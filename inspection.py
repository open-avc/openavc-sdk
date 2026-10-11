"""Read the locally launched integration without product-specific commands."""

import time
from openavc_oem._protocol import PROTOCOL


def inspect_product(request, expected_model):
    result = {"feedback": "unavailable", "identity": None, "capabilities": None, "readback": None}
    deadline = time.monotonic() + 4
    while True:
        status, description = request('/v1/description')
        if status == 200:
            if (type(description) is not dict or description.get('protocol') != PROTOCOL
                    or description.get('capabilities') != expected_model
                    or type(description.get('identity')) is not str or not description['identity']):
                raise ValueError('The running integration does not describe the generated product model.')
            model = description['capabilities']
            result['identity'] = description['identity']
            result['capabilities'] = {
                'product': {key: model[key] for key in ['id', 'name', 'manufacturer', 'category', 'version', 'description'] if key in model},
                'commands': {name: {key: command[key] for key in ['label', 'description', 'params'] if key in command}
                             for name, command in model.get('commands', {}).items()},
                'state_variables': model.get('state_variables', {}),
                'child_entity_types': model.get('child_entity_types', {}),
            }
            status, snapshot = request('/v1/snapshot')
            if status == 200:
                if (type(snapshot) is not dict or snapshot.get('identity') != result['identity']
                        or snapshot.get('quality') != 'current' or type(snapshot.get('state')) is not dict
                        or type(snapshot.get('children')) is not list):
                    raise ValueError('The running integration did not return current product readback.')
                result.update(feedback='current', readback={'state': snapshot['state'], 'children': snapshot['children']})
                return result
        if status != 503:
            raise ValueError('The local integration could not be inspected. Read the endpoint log.')
        if time.monotonic() >= deadline:
            return result
        time.sleep(.05)
