"""Run the product integration using a firmware-owned configuration file."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .manager import Manager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Product supervision requires Linux. Use the in-process hardware example on macOS.')
    asyncio.run(Manager(json.loads(args.config.read_text())).run())


if __name__ == '__main__':
    main()
