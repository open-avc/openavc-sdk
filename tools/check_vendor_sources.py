"""Verify the vendored native sources against their recorded upstream identities."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def check(upstream=False):
    record = json.loads((ROOT / 'sdk/dependencies.json').read_text(encoding='utf-8'))
    expected = set()
    for component in record['components']:
        for item in component['files']:
            path = ROOT / item['file']
            if not path.resolve().is_relative_to(ROOT):
                raise ValueError('Vendored source path leaves the SDK checkout.')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('Vendored source differs from its record: ' + item['file'])
            if upstream:
                url = component['repository'].replace('https://github.com/', 'https://raw.githubusercontent.com/')
                url += '/' + component['revision'] + '/' + item['upstream_path']
                with urllib.request.urlopen(url, timeout=60) as response:
                    if response.read() != raw:
                        raise ValueError('Vendored source differs from upstream: ' + item['file'])
            expected.add(item['file'])
    actual = {path.relative_to(ROOT).as_posix() for folder in ['civetweb', 'json']
              for path in (ROOT / 'sdk' / folder).rglob('*') if path.is_file()}
    if actual != expected:
        raise ValueError('The vendored source file set differs from its record.')
    print(f'Verified {len(expected)} vendored source files.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', action='store_true', help='Also compare bytes with the pinned upstream revisions.')
    check(parser.parse_args().upstream)
