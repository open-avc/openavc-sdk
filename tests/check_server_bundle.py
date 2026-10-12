"""Verify a real delivered bundle against wheel bytes and an offline installation."""

from email.parser import BytesParser
import hashlib
from importlib import metadata, resources
import json
from pathlib import Path
import sys
from zipfile import ZipFile

bundle = Path(sys.argv[1])
record = json.loads((bundle / 'bundle.json').read_text())
bom = json.loads((bundle / 'python-packages.cdx.json').read_text())
components = {next(p['value'] for p in c['properties'] if p['name'] == 'openavc:wheel:filename'): c
              for c in bom['components']}
assert set(components) == set(record['wheels']) == {p.name for p in (bundle / 'wheels').glob('*.whl')}
for filename, sha in record['wheels'].items():
    path = bundle / 'wheels' / filename
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
    assert components[filename]['hashes'] == [{'alg': 'SHA-256', 'content': sha}]
    with ZipFile(path) as wheel:
        entry, = [name for name in wheel.namelist() if name.endswith('.dist-info/METADATA')]
        declared = BytesParser().parsebytes(wheel.read(entry))
    assert components[filename]['name'] == declared['Name']
    assert components[filename]['version'] == declared['Version'] == metadata.version(declared['Name'])
for name, sha in record['inventories'].items():
    assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == sha
assert record['build_image_id'] == (bundle / 'build-image.txt').read_text().strip()
for interface in ['programmer', 'simulator']:
    assert resources.files('openavc').joinpath(f'web/{interface}/dist/index.html').is_file()
assert resources.files('openavc_oem').joinpath('manager.py').is_file()
print(f'Verified {len(components)} delivered wheels against their inventory and offline installation.')
