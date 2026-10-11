"""Generate typed state publishers from the same validated equipment model."""

import json


def publishers(model, identifier, declare):
    header = ['/* State values are copied during each call. Snapshot ownership stays',
              ' * with the SDK. Report every present child in each readback. */']
    source = []
    groups = [(None, None, model.get('state_variables', {}))]
    for kind, definition in model.get('child_entity_types', {}).items():
        identifier(kind, 'child_entity_types.' + kind)
        id_kind = definition.get('id_format', {}).get('type', 'integer')
        groups.append((kind, id_kind, definition.get('state_variables', {})))
    for child, id_kind, fields in groups:
        for field, rule in fields.items():
            where = ('state_variables' if child is None else f'child_entity_types.{child}.state_variables') + '.' + field
            field_name = identifier(field, where)
            stem = 'oavc_state_' + (identifier(child, where) + '_' if child else '') + field_name
            declare(stem, where)
            id_type = 'const char *' if id_kind == 'string' else 'int64_t '
            signature = ['oavc_snapshot *snapshot'] + ([id_type + 'child_id'] if child else [])
            child_id = ('oavc_child_string(child_id)' if id_kind == 'string' else 'oavc_child_number(child_id)') if child else 'oavc_child_number(0)'
            target = f'snapshot, {json.dumps(child) if child else "NULL"}, {child_id}, {json.dumps(field)}'
            kind = rule.get('type')
            if kind == 'enum':
                typename = stem + '_value'
                declare(typename, where)
                choices = [v['value'] if isinstance(v, dict) else v for v in rule.get('values', [])]
                if not choices or len(choices) != len({json.dumps(value) for value in choices}):
                    raise ValueError(f'{where}.values: supply distinct enum values.')
                constants = []
                for value in choices:
                    constant = (stem + '_' + identifier(str(value), where + '.values')).upper()
                    declare(constant, where)
                    constants.append(constant)
                header += ['typedef enum {', ',\n'.join('    ' + value for value in constants), '} ' + typename + ';']
                signature.append(typename + ' value')
                body = ['    switch (value) {']
                for constant, value in zip(constants, choices):
                    function = 'boolean' if isinstance(value, bool) else 'signed_integer' if isinstance(value, int) else 'number' if isinstance(value, float) else 'string'
                    literal = str(int(value)) if isinstance(value, bool) else json.dumps(value)
                    body.append(f'        case {constant}: oavc_state_{function}({target}, {literal}); break;')
                body += ['        default: oavc_snapshot_invalidate(snapshot); break;', '    }']
            else:
                types = {'number': 'double', 'float': 'double', 'integer': 'int64_t', 'boolean': 'int', 'string': 'const char *'}
                if kind not in types:
                    raise ValueError(f'{where}.type: {kind!r} has no C state publisher mapping.')
                signature.append(types[kind] + ' value')
                function = 'signed_integer' if kind == 'integer' else 'number' if kind == 'float' else kind
                body = []
                if kind == 'boolean':
                    body.append('    if (value != 0 && value != 1) { oavc_snapshot_invalidate(snapshot); return; }')
                body.append(f'    oavc_state_{function}({target}, value);')
            declaration = f'void {stem}({", ".join(signature)})'
            header.append(declaration + ';')
            source += [declaration + ' {', *body, '}']
    return header, source
