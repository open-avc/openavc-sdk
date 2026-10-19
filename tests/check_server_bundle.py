"""Verify a real delivered bundle against wheel bytes and an offline installation."""

from email.parser import BytesParser
import hashlib
from importlib import metadata, resources
import json
import os
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
contents = bundle / 'wheel-contents'
assert set(record['wheel_contents']) == {path.name for path in contents.iterdir()}
for name, sha in record['wheel_contents'].items():
    assert hashlib.sha256((contents / name).read_bytes()).hexdigest() == sha
index = json.loads((contents / 'index.json').read_text())
supplements = json.loads((Path(__file__).resolve().parents[1] / 'licenses/wheels/index.json').read_text())['notices']
assert set(index['wheels']) == set(record['wheels'])
for name, entry in index['wheels'].items():
    assert entry['sha256'] == record['wheels'][name]
    expected_notices = [item for item in supplements if entry['sha256'] in item['wheel_sha256']]
    for original in expected_notices:
        matches = [item for item in entry['notices'] if item.get('origin') == original['origin']]
        assert any(all(item.get(field) == original[field] for field in
                       ['sha256', 'component', 'evidence', 'dependency_scope']) for item in matches), original['origin']
    if entry['name'] == 'openavc':
        assert any(item['sha256'] == record['core_build_inputs']['LICENSE'] for item in entry['notices'])
    with ZipFile(bundle / 'wheels' / name) as wheel:
        native = {item.filename for item in wheel.infolist()
                  if not item.is_dir() and wheel.read(item)[:4] == b'\x7fELF'}
        assert {item['member'] for item in entry['native_members']} == native
        for item in entry['native_members']:
            payload = wheel.read(item['member'])
            assert hashlib.sha256(payload).hexdigest() == item['sha256']
            assert len(payload) == item['size']
        for notice in entry['notices']:
            payload = (contents / notice['file']).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == notice['sha256']
            if 'member' in notice:
                assert payload == wheel.read(notice['member'])
    assert entry['notices'], name
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
system_inputs = bundle / 'build-inputs/system-inputs'
assert (system_inputs / 'debian.sources').read_bytes() == (
    Path(__file__).resolve().parents[1] / 'tools/debian.sources').read_bytes()
releases = list(system_inputs.glob('*_InRelease'))
assert len(releases) == 3
assert all(path.read_bytes().startswith(b'-----BEGIN PGP SIGNED MESSAGE-----') for path in releases)
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
    # Exercise the installed command away from this checkout, so source files
    # cannot hide missing or mismatched delivered package contents.
    command = [sys.executable, '-m', 'openavc_oem.preflight', '--bundle',
               str(bundle.resolve()), '--expect-core', record['core']['core_head']]
    preflight = subprocess.run(command, cwd=scratch, text=True, capture_output=True)
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr
    result = json.loads(preflight.stdout)
    assert result['status'] == 'passed'
    assert result['core_commit'] == record['core']['core_head']
    changed = Path(result['packages']['openavc']['origin']) / 'core/host_policy.py'
    original = changed.read_bytes()
    try:
        changed.write_bytes(original + b'\n# Deliberate candidate payload mismatch.\n')
        refused = subprocess.run(command, cwd=scratch, text=True, capture_output=True)
        assert refused.returncode == 1, refused.stdout + refused.stderr
        assert json.loads(refused.stdout)['status'] == 'refused'
        assert 'core/host_policy.py' in refused.stdout
    finally:
        changed.write_bytes(original)
    assert changed.read_bytes() == original
    result['mismatched_candidate'] = json.loads(refused.stdout)
    result['original_source_restored'] = True
    (bundle / 'installed-preflight.json').write_text(json.dumps(result, indent=2) + '\n')
    # Exercise the literal installed selection command, not the checkout API.
    # This same-build fixture tests admission only, never a running upgrade.
    from openavc_oem.setup_inventory import FirmwareSetupSelection, ServerSelection, selection_record

    data = scratch / 'product'
    (data / 'server').mkdir(parents=True)
    package_root = Path(result['packages']['openavc']['origin'])
    selected = ServerSelection(hashlib.sha256(b'fixture-config').hexdigest(),
        hashlib.sha256(b'fixture-planned-launch').hexdigest(), Path(sys.executable),
        data, package_root, package_root.parent)
    selection = selection_record(FirmwareSetupSelection(
        hashlib.sha256(b'fixture-attempt').hexdigest(), selected, selected, (),
        hashlib.sha256(b'fixture-no-activation').hexdigest()))
    selection_path = scratch / 'selection.json'
    selection_path.write_text(json.dumps(selection))
    selected_command = [*command, '--setup-selection', str(selection_path)]
    selection_checks = {}
    selection_evidence = bundle / 'installed-selection-evidence'
    selection_evidence.mkdir(exist_ok=True)

    def selected_run(name, expected):
        (selection_evidence / (name + '-command.json')).write_text(json.dumps(selected_command) + '\n')
        try:
            response = subprocess.run(selected_command, cwd=scratch, capture_output=True, timeout=90)
        except subprocess.TimeoutExpired as error:
            (selection_evidence / (name + '.stdout')).write_bytes(error.stdout or b'')
            (selection_evidence / (name + '.stderr')).write_bytes(error.stderr or b'')
            raise
        (selection_evidence / (name + '.stdout')).write_bytes(response.stdout)
        (selection_evidence / (name + '.stderr')).write_bytes(response.stderr)
        report = json.loads(response.stdout)
        assert response.returncode == (1 if expected == 'refused' else 0), response.stdout + response.stderr
        assert report['status'] == expected and report['activation_authorized'] is False
        assert report.get('installation_complete') is not True
        if expected == 'assessed':
            assert report['setup_inventory']['selection'] == selection
            assert report['packages'] == result['packages']
        selection_checks[name] = report

    selected_run('baseline', 'assessed')
    archive_path = data / 'server/saved_projects/unknown/bundle.zip'
    archive_path.parent.mkdir(parents=True)
    unknown = b'raise RuntimeError("an inactive source must not execute")\n'
    with ZipFile(archive_path, 'x') as archive:
        archive.writestr('drivers/acme_unqualified.py', unknown)
    archive_before = archive_path.read_bytes()
    selected_run('unknown_inactive_source', 'refused')
    assert hashlib.sha256(unknown).hexdigest() in json.dumps(selection_checks['unknown_inactive_source'])
    assert archive_path.read_bytes() == archive_before
    selection_path.write_text('[' * 2000 + '0' + ']' * 2000)
    selected_run('excessive_nesting', 'refused')
    selection_path.unlink()
    if hasattr(os, 'mkfifo'):
        os.mkfifo(selection_path)
        selected_run('nonregular_selection', 'refused')
    (bundle / 'installed-selection.json').write_text(json.dumps({
        'scope': 'same_build_conditional_command_no_activation', 'checks': selection_checks,
        'unknown_source_preserved': True}, indent=2) + '\n')
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
