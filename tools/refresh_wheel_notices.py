"""Reproduce retained notices from the reviewed, hash-pinned source list."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def materialize(sources, cache, offline=False):
    """Read source archives as data; do not extract paths or execute build code."""
    cache.mkdir(parents=True, exist_ok=True)
    files = {}
    notices = []
    for source in sources:
        expected = source['sha256']
        if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
            raise ValueError('Invalid source digest.')
        local = cache / expected
        if local.exists():
            raw = local.read_bytes()
        elif offline:
            raise ValueError('Source is absent from the offline cache: ' + source['url'])
        else:
            if not source['url'].startswith('https://'):
                raise ValueError('Source URLs must use HTTPS.')
            with urllib.request.urlopen(source['url'], timeout=60) as response:
                raw = response.read()
        if sha256(raw) != expected:
            raise ValueError('Source digest mismatch: ' + source['url'])
        local.write_bytes(raw)
        if source['kind'] == 'tar':
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                names = [member.name for member in archive]
                payloads = []
                for selected in source['files']:
                    member = selected['member']
                    if names.count(member) != 1 or not archive.getmember(member).isfile():
                        raise ValueError('Notice must identify one regular source file: ' + member)
                    payloads.append((selected, archive.extractfile(member).read()))
        elif source['kind'] == 'file':
            if len(source['files']) != 1:
                raise ValueError('A plain source file must select one notice.')
            payloads = [(source['files'][0], raw)]
        else:
            raise ValueError('Unsupported source format.')
        for selected, content in payloads:
            digest = sha256(content)
            if digest != selected['sha256']:
                raise ValueError('Notice digest mismatch: ' + selected['member'])
            filename = digest + '.txt'
            files[filename] = content
            for target in source['applies_to']:
                notices.append({**target, 'component': source['component'],
                                'evidence': source['evidence'],
                                'file': filename, 'sha256': digest,
                                'origin': source['url'] + ('#' + selected['member'] if source['kind'] == 'tar' else ''),
                                'source_sha256': expected, 'source_format': source['kind']})
    files['index.json'] = (json.dumps({'format': 1, 'notices': notices}, indent=2) + '\n').encode()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, default=ROOT / 'tools/wheel-notice-sources.json')
    parser.add_argument('--out', type=Path, default=ROOT / 'licenses/wheels')
    parser.add_argument('--cache', type=Path, default=ROOT / 'build/notice-source-cache')
    parser.add_argument('--offline', action='store_true', help='Read only previously verified cache files.')
    parser.add_argument('--check', action='store_true', help='Compare without changing the retained notices.')
    args = parser.parse_args()
    source = json.loads(args.sources.read_text(encoding='utf-8'))
    if source['format'] != 1:
        raise ValueError('Unsupported source-list format.')
    files = materialize(source['sources'], args.cache, args.offline)
    if args.check:
        if {path.name for path in args.out.iterdir()} != set(files):
            raise ValueError('Retained notice file set differs from the source list.')
        for name, content in files.items():
            if (args.out / name).read_bytes() != content:
                raise ValueError('Retained notice differs from its source: ' + name)
    else:
        args.out.mkdir(parents=True, exist_ok=True)
        extras = {path.name for path in args.out.iterdir()} - set(files)
        if extras:
            raise ValueError('Use an empty output directory; it contains files outside this source list.')
        for name, content in files.items():
            (args.out / name).write_bytes(content)
    print(f'Verified {len(source["sources"])} source inputs and {len(files) - 1} notice files.')


if __name__ == '__main__':
    main()
