"""Expand a product-only description through the canonical OpenAVC validator.

Product fields retain their canonical meaning; the SDK supplies transport.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

from generate import unique_fields


def prepare(product_path, core, out):
    sys.path.insert(0, str(core.resolve()))
    from openavc.drivers import avcdriver_semantic
    if not Path(avcdriver_semantic.__file__).resolve().is_relative_to(core.resolve()):
        raise ValueError('Validator came from a different OpenAVC checkout.')
    product = json.loads(product_path.read_bytes(), object_pairs_hook=unique_fields)
    if not isinstance(product, dict):
        raise ValueError('Product description must be a JSON object.')
    supported = {'id', 'name', 'manufacturer', 'category', 'version', 'author',
                 'source_url', 'description', 'state_variables', 'child_entity_types', 'commands',
                 'device_settings', 'quick_actions', 'actions', 'capabilities', 'tags',
                 'compatible_models'}
    for key in product:
        if key not in supported:
            raise ValueError(f'{key}: describe product capabilities here; the SDK supplies connection behavior.')
    model = copy.deepcopy(product)
    commands = model.setdefault('commands', {})
    if not isinstance(commands, dict):
        raise ValueError('commands: use a JSON object of named commands.')
    for name, command in commands.items():
        if not isinstance(command, dict):
            raise ValueError(f'commands.{name}: use a JSON object.')
        for key in command:
            if key not in {'label', 'description', 'help', 'params', 'sets', 'query_for', 'confirm', 'restarts_device_for'}:
                raise ValueError(f'commands.{name}.{key}: the SDK supplies command transport; implement hardware behavior in its callback.')
        command.update(method='POST', path='/v1/command')
    states = model.setdefault('state_variables', {})
    if not isinstance(states, dict):
        raise ValueError('state_variables: use a JSON object of named states.')
    model['transport'] = 'http'
    model['config_schema'] = {
        'url': {'type': 'string', 'label': 'Endpoint URL', 'required': True},
        'token': {'type': 'string', 'label': 'Endpoint Credential', 'required': True, 'secret': True},
    }
    errors = avcdriver_semantic.validate_driver_definition(model, strict=True)
    if errors:
        raise ValueError('OpenAVC model validation failed:\n' + '\n'.join(errors))
    # Validate before touching any output, including an earlier successful build.
    value = json.dumps(model, indent=2) + '\n'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(value, encoding='utf-8')
    provenance = {'product_sha256': hashlib.sha256(product_path.read_bytes()).hexdigest(),
                  'compiler_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'model_sha256': hashlib.sha256(value.encode()).hexdigest()}
    out.with_suffix('.provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product', type=Path, required=True)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.product, args.core, args.out)
    except (ValueError, OSError, ImportError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
