"""Read the product's saved OpenAVC settings without inferring permission."""

import json

FLAGS = ('discovery', 'remote_access', 'server', 'programmer', 'panel')


def defaults(identity, initial=False):
    return {'format': 1, 'identity': identity, 'revision': 0,
            **{key: initial and key != 'server' for key in FLAGS}}


def read_state(path, identity):
    raw = path.read_bytes()
    if len(raw) > 16384:
        raise ValueError('Product settings exceed 16 KiB.')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate product setting.')
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    if (not isinstance(value, dict) or set(value) != {*FLAGS, 'format', 'revision', 'identity'}
            or type(value['format']) is not int or value['format'] != 1
            or type(value['revision']) is not int or value['revision'] < 0
            or value['identity'] != identity or any(type(value[key]) is not bool for key in FLAGS)):
        raise ValueError('Product settings are invalid or belong to another unit.')
    return value
