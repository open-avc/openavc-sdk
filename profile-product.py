"""Measure the processes on this host while you exercise your product."""
import argparse
from pathlib import Path

from resource_profile import profile


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, action='append', required=True, help='Local process to include, repeatable; observed descendants included')
    parser.add_argument('--duration', type=float, default=60, help='Measurement seconds (default: 60)')
    parser.add_argument('--interval', type=float, default=1, help='Sample interval seconds (default: 1)')
    parser.add_argument('--data-dir', type=Path, help='Optional product data directory to measure without reading file contents')
    parser.add_argument('--label', default='', help='Your firmware/build label for this measurement')
    parser.add_argument('--workload', default='', help='Your description of the AV workload exercised during measurement')
    parser.add_argument('--artifact', type=Path, action='append', default=[], help='Record a firmware file or executable SHA-256 before measurement; repeatable')
    parser.add_argument('--out', type=Path, required=True, help='New private evidence directory')
    args = parser.parse_args()
    try:
        result = profile(args.pid, args.out, args.duration, args.interval, args.data_dir,
                         label=args.label, workload=args.workload, artifacts=args.artifact)
    except ValueError as error:
        parser.exit(1, str(error) + '\n')
    except Exception:
        parser.exit(1, 'Measurement failed. Check the selected processes, access permissions and output directory. Read report.json if created.\n')
    print('Measurement complete: ' + str(args.out / 'report.json'))
    print('Run this on the target firmware with its normal AV workload before drawing hardware sizing conclusions.')
