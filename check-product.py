"""Inspect your running equipment integration, or run explicitly selected command checks."""
import argparse
from pathlib import Path

from product_checks import CheckFailed, Client, read_json, run_checks


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='HTTPS equipment endpoint')
    parser.add_argument('--credentials', type=Path, required=True, help='Private JSON file with username/password or local token')
    parser.add_argument('--ca-file', type=Path, help='CA certificate when not in system trust')
    parser.add_argument('--model', type=Path, required=True, help='Generated model.json from your build')
    parser.add_argument('--checks', type=Path, help='Explicit command/readback checks; omitted means read-only')
    parser.add_argument('--out', type=Path, required=True, help='New private evidence directory')
    args = parser.parse_args()
    try:
        client = Client(args.url, read_json(args.credentials), args.ca_file)
        result = run_checks(client, read_json(args.model), args.out,
                            read_json(args.checks) if args.checks else None)
    except CheckFailed as error:
        parser.exit(1, str(error) + '\nRead report.json if created.\n')
    except Exception:
        parser.exit(1, 'Product checks did not pass. Read report.json if created, and check your URL, TLS trust, input files and output path. Do not replay an interrupted command.\n')
    print('Product checks passed. Report: ' + str(args.out / 'report.json'))
    print('Coverage: protocol and reported state. Verify physical effects independently on your product.')
