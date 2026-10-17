"""Prepare one product's protected identity and local management credential."""
import argparse
import json
from pathlib import Path

from openavc_oem.provisioning import provision_unit, rotate_management_token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='Private persistent directory for this unit')
    parser.add_argument('--identity', help='Existing unique factory identity; generated once if omitted')
    parser.add_argument('--rotate-management-token', action='store_true',
                        help='Replace local management access; stop management before using this option')
    args = parser.parse_args()
    if args.rotate_management_token and args.identity is not None:
        parser.error('Token replacement preserves identity; omit --identity.')
    try:
        unit = rotate_management_token(args.out) if args.rotate_management_token else provision_unit(args.out, identity=args.identity)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps({'identity': unit.identity, 'management_token_file': str(unit.management_token_file),
                      'created': unit.created, 'management_token_replaced': args.rotate_management_token}, indent=2))


if __name__ == '__main__':
    main()
