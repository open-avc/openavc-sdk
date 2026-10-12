"""Compare two completed resource measurements; optionally retain the comparison."""
import argparse
import json
import os
from pathlib import Path

from profile_comparison import compare


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True, help='Earlier profile-product.py report.json')
    parser.add_argument('--candidate', type=Path, required=True, help='Later profile-product.py report.json')
    parser.add_argument('--out', type=Path, help='New private JSON comparison file; omitted prints only')
    args = parser.parse_args()
    try:
        result = compare(args.baseline, args.candidate)
        text = json.dumps(result, indent=2, allow_nan=False) + '\n'
        if args.out:
            with os.fdopen(os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
        print(text, end='')
    except (ValueError, OSError, RecursionError) as error:
        parser.exit(1, 'Comparison failed: ' + str(error) + '\n')
