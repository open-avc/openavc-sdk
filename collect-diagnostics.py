"""Preview selected product diagnostics; export that same preview with --out."""
import argparse
import json
from pathlib import Path

from diagnostic_report import summarize
from product_checks import read_json


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', type=Path, help='JSON from product management: Download diagnostics')
    parser.add_argument('--checks', type=Path, help='report.json from the product checker')
    parser.add_argument('--resources', type=Path, help='report.json from the resource profiler')
    parser.add_argument('--environment', type=Path, help='Report from the environment/toolchain doctor')
    parser.add_argument('--server-bundle', type=Path, help='Offline server bundle directory to check and identify')
    parser.add_argument('--health-log', type=Path, action='append', default=[],
                        help='Product logs/health.jsonl segment; repeat oldest first, up to eight files')
    parser.add_argument('--network', action='store_true',
                        help='Include this host\'s interface status and address counts, without addresses or names')
    parser.add_argument('--out', type=Path, help='Explicit export to a new file; omitted means preview only')
    args = parser.parse_args()
    try:
        report = summarize(*(read_json(path, limit=16 * 1024 * 1024) if path else None
                             for path in (args.status, args.checks, args.resources)), server_bundle=args.server_bundle,
                           health_logs=args.health_log, network=args.network,
                           environment=read_json(args.environment, limit=16 * 1024 * 1024) if args.environment else None)
        text = json.dumps(report, indent=2, allow_nan=False) + '\n'
        if args.out:
            with args.out.open('x') as stream:
                stream.write(text)
        print(text, end='')
    except (ValueError, OSError, RecursionError):
        parser.exit(1, 'Diagnostics could not be collected. Check the selected files, server bundle and local read permissions, and use a new export filename.\n')
