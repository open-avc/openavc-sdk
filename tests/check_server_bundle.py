"""Verify a real delivered bundle against wheel bytes and an offline installation."""

from email.parser import BytesParser
import hashlib
from importlib import metadata, resources
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
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
supplied = bundle / 'publisher-sboms'
assert set(record['publisher_sboms']) == {path.name for path in supplied.iterdir()}
for name, sha in record['publisher_sboms'].items():
    assert hashlib.sha256((supplied / name).read_bytes()).hexdigest() == sha
index = json.loads((supplied / 'index.json').read_text())
assert set(index['wheels']) == set(record['wheels'])
for name, entry in index['wheels'].items():
    assert entry['sha256'] == record['wheels'][name]
    with ZipFile(bundle / 'wheels' / name) as wheel:
        metadata_file, = [n for n in wheel.namelist() if re.fullmatch(r'[^/]+\.dist-info/METADATA', n)]
        prefix = metadata_file.removesuffix('METADATA') + 'sboms/'
        originals = {item.filename for item in wheel.infolist()
                     if item.filename.startswith(prefix) and not item.is_dir()}
        assert {document['member'] for document in entry['documents']} == originals
        for document in entry['documents']:
            copied = (supplied / document['file']).read_bytes()
            assert copied == wheel.read(document['member'])
            assert hashlib.sha256(copied).hexdigest() == document['sha256']
for name, sha in record['build_inputs'].items():
    assert hashlib.sha256((bundle / 'build-inputs' / name).read_bytes()).hexdigest() == sha
locked = {}
for line in (bundle / 'build-inputs/requirements-server.txt').read_text().splitlines():
    if not line or line.startswith('#'):
        continue
    requirement, *hashes = line.split()
    name, version = requirement.split('==')
    locked[name] = (version, {value.removeprefix('--hash=sha256:') for value in hashes})
delivered = set()
for component in components.values():
    name = re.sub(r'[-_.]+', '-', component['name']).lower()
    if name in {'openavc', 'openavc-oem'}:
        continue
    version, hashes = locked[name]
    assert component['version'] == version
    assert component['hashes'][0]['content'] in hashes
    delivered.add(name)
assert delivered == set(locked)
assert record['build_image_id'] == (bundle / 'build-image.txt').read_text().strip()
for interface in ['programmer', 'simulator']:
    assert resources.files('openavc').joinpath(f'web/{interface}/dist/index.html').is_file()
    folder = bundle / 'build-inputs/browser-builds' / interface
    browser = json.loads((folder / 'report.json').read_text())
    assert browser['outputs'] and browser['observations'] and browser['packages']
    installed = resources.files('openavc').joinpath(f'web/{interface}')
    for name, sha in browser['inputs'].items():
        assert hashlib.sha256(installed.joinpath(name).read_bytes()).hexdigest() == sha
    for name, sha in browser['outputs'].items():
        assert hashlib.sha256(installed.joinpath('dist', name).read_bytes()).hexdigest() == sha
    observed = {chunk['file']: chunk for observation in browser['observations'] for chunk in observation['chunks']}
    assert {name for name in browser['outputs'] if name.endswith('.js')} <= set(observed)
    for name, chunk in observed.items():
        assert browser['outputs'][name] == chunk['sha256']
    lock = json.loads((bundle / f'build-inputs/{interface}-package-lock.json').read_text())
    packages = dict(browser['packages'])
    for name, asset in browser['copied_assets'].items():
        assert hashlib.sha256(resources.files('openavc').joinpath(name).read_bytes()).hexdigest() == asset['sha256']
        package = asset['package']
        packages[package['name'] + '@' + package['version']] = package
    for package in packages.values():
        assert any(path.endswith('node_modules/' + package['name']) and entry.get('version') == package['version']
                   for path, entry in lock['packages'].items())
        for notice in package['notices']:
            assert hashlib.sha256((folder / notice['file']).read_bytes()).hexdigest() == notice['sha256']
assert resources.files('openavc_oem').joinpath('manager.py').is_file()
with tempfile.TemporaryDirectory(prefix='openavc-lock-check-') as temporary:
    scratch = Path(temporary)
    bad_lock = scratch / 'changed-hash.txt'
    name = next(iter(locked))
    bad_lock.write_text(f'{name}=={locked[name][0]} --hash=sha256:{"0" * 64}\n')
    refused = subprocess.run([
        sys.executable, '-m', 'pip', 'download', '--no-index', '--only-binary=:all:',
        '--find-links=' + str(bundle.resolve() / 'wheels'), '--require-hashes',
        '-r', str(bad_lock), '--dest', str(scratch / 'download')],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    assert refused.returncode != 0 and 'DO NOT MATCH THE HASHES' in refused.stdout, refused.stdout
    assert not list((scratch / 'download').glob('*.whl'))
print('Changed dependency hash refused before download; original bundle unchanged.')
print(f'Verified {len(components)} delivered wheels against their inventory and offline installation.')
