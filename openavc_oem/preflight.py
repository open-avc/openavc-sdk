"""Check an installed candidate against its trusted offline server bundle.

Run in the candidate Python environment before stopping the current firmware.
By default this reads package files only. Supplied source paths or an explicit
server selection add a canonical read-only source assessment in the verified
candidate child. No mode starts OpenAVC or grants firmware activation. The host
owns bundle authenticity, actual launch association and firmware acceptance.
"""
import argparse
import contextlib
from email.parser import BytesParser
import hashlib
from importlib.util import find_spec
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import threading
from zipfile import BadZipFile, ZipFile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def package_root(name):
    spec = find_spec(name)
    locations = list(spec.submodule_search_locations or ()) if spec else []
    if len(locations) != 1:
        raise ValueError(f'The candidate must contain one installed {name} package.')
    return Path(locations[0]).resolve(strict=True)


def check_payload(wheel, package, root):
    expected = {}
    for member in wheel.infolist():
        if member.is_dir() or not member.filename.startswith(package + '/'):
            continue
        relative = member.filename[len(package) + 1:]
        path = PurePosixPath(relative)
        if (not relative or path.is_absolute() or '..' in path.parts
                or '\\' in relative or relative in expected):
            raise ValueError('Invalid or repeated package member in the server wheel.')
        expected[relative] = hashlib.sha256(wheel.read(member)).hexdigest()
    if '__init__.py' not in expected:
        raise ValueError(f'The server wheel does not contain {package}.')
    actual = {}
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if '__pycache__' in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError(f'The candidate package contains a symbolic link: {package}/{relative}')
        if path.is_file():
            actual[relative.as_posix()] = digest(path)
    differences = sorted(name for name in expected.keys() | actual.keys()
                         if expected.get(name) != actual.get(name))
    if differences:
        raise ValueError(f'The installed {package} files differ from the selected bundle: '
                         + ', '.join(differences[:5]))
    return len(expected)


_PACKAGE_BOOTSTRAP = '''import importlib.util, pathlib, sys
roots = [pathlib.Path(sys.argv.pop(1)), pathlib.Path(sys.argv.pop(1))]
for name, root in zip(('openavc', 'openavc_oem'), roots):
    spec = importlib.util.spec_from_file_location(name, root / '__init__.py',
                                               submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
'''

_CENSUS_CHILD = '''import json
from openavc_oem.setup_inventory import selection_from_payload, assess_server_selection
raw = sys.stdin.buffer.read(262145)
result = assess_server_selection(selection_from_payload(raw),
    resource=pathlib.Path(sys.argv[1]), expected_resource_sha256=sys.argv[2], target_sha256=sys.argv[3])
result['evaluator'] = {'python': sys.executable,
    'core_module': sys.modules['openavc.drivers.setup_preflight'].__file__,
    'sdk_module': sys.modules['openavc_oem.setup_inventory'].__file__}
print(json.dumps(result))
sys.exit(1 if result['status'] == 'refused' else 0)
'''


_REPORT_BYTES = 32 * 1024 * 1024
_ERROR_BYTES = 64 * 1024
_CHILD_TIMEOUT = 60


def _run_evaluator(command, payload):
    """Drain both pipes with finite storage and reap the child on every outcome."""
    output, errors = bytearray(), bytearray()
    failures = []
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as process:
        def stop():
            with contextlib.suppress(ProcessLookupError):
                process.kill()

        def read(stream, retained, limit):
            try:
                with stream:
                    while chunk := stream.read1(65536):
                        if len(retained) + len(chunk) > limit:
                            failures.append('The setup evaluator exceeded its output byte limit.')
                            stop()
                            return
                        retained.extend(chunk)
            except OSError:
                failures.append('The setup evaluator output could not be read.')
                stop()

        def write():
            try:
                with process.stdin:
                    if payload is not None:
                        process.stdin.write(payload.encode('utf-8'))
            except BrokenPipeError:
                # A refusing or failed child may exit before consuming input.
                pass
            except OSError:
                failures.append('The setup evaluator request could not be written.')
                stop()

        workers = [threading.Thread(target=read, args=(process.stdout, output, _REPORT_BYTES)),
                   threading.Thread(target=read, args=(process.stderr, errors, _ERROR_BYTES)),
                   threading.Thread(target=write)]
        started = []
        try:
            for worker in workers:
                worker.start()
                started.append(worker)
            process.wait(timeout=_CHILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            failures.append('The setup evaluator exceeded its time limit.')
            stop()
        except BaseException:
            stop()
            raise
        finally:
            # Only the verified, non-spawning evaluator runs here. Joining its
            # pipe readers also catches overflow after the child has exited.
            process.wait()
            for worker in started:
                worker.join()
        if failures:
            raise ValueError(failures[0])
        try:
            return subprocess.CompletedProcess(command, process.returncode,
                                               output.decode('utf-8'), errors.decode('utf-8'))
        except UnicodeDecodeError:
            raise ValueError('The setup evaluator returned invalid UTF-8.') from None


def check(bundle, expected_core, *, setup_sources=None, setup_selection=None):
    if setup_sources is not None and setup_selection is not None:
        raise ValueError('Select either supplied source paths or the fixed server census.')
    if not re.fullmatch(r'[0-9a-f]{40}', expected_core):
        raise ValueError('Supply the complete core commit from your qualified firmware build.')
    record = json.loads((bundle / 'bundle.json').read_text(encoding='utf-8'))
    if record['core']['core_head'] != expected_core:
        raise ValueError('The server bundle does not match the selected core commit.')
    files = record['wheels']
    if not isinstance(files, dict) or not files:
        raise ValueError('The server bundle has no wheel inventory.')
    packages = {'openavc': 'openavc', 'openavc-oem': 'openavc_oem'}
    checked = {}
    setup_resource_sha = None
    for name, sha in files.items():
        if Path(name).name != name or '\\' in name or not name.endswith('.whl'):
            raise ValueError('Invalid wheel name in the server bundle.')
        path = bundle / 'wheels' / name
        if digest(path) != sha:
            raise ValueError(f'The server bundle wheel hash does not match: {name}')
        with ZipFile(path) as wheel:
            metadata = [entry for entry in wheel.namelist()
                        if re.fullmatch(r'[^/]+\.dist-info/METADATA', entry)]
            if len(metadata) != 1:
                raise ValueError(f'The wheel must contain exactly one metadata record: {name}')
            declared = BytesParser().parsebytes(wheel.read(metadata[0]))
            distribution = re.sub(r'[-_.]+', '-', declared.get('Name', '')).lower()
            if distribution not in packages:
                continue
            package = packages[distribution]
            if package in checked:
                raise ValueError(f'The server bundle repeats the {package} package.')
            root = package_root(package)
            checked[package] = {'origin': str(root), 'wheel': name, 'wheel_sha256': sha,
                                'checked_files': check_payload(wheel, package, root)}
            if package == 'openavc' and (setup_sources is not None or setup_selection is not None):
                member = 'openavc/drivers/setup-compatibility.json'
                if member not in wheel.namelist():
                    raise ValueError('The selected core has no setup source qualification resource.')
                setup_resource_sha = hashlib.sha256(wheel.read(member)).hexdigest()
    if set(checked) != set(packages.values()):
        raise ValueError('The server bundle must contain both OpenAVC and its manufacturer integration.')
    report = {'status': 'passed', 'core_commit': expected_core, 'packages': checked}
    if setup_sources is not None or setup_selection is not None:
        # Invoke the matching core without site hooks, inherited module state,
        # or installed bytecode caches (which the payload check excludes).
        # -B alone prevents writes but still reads an existing unchecked cache.
        # Bootstrap exact packages; never put unchecked sibling directories on
        # the child's search path, even when the two packages live separately.
        core_root = Path(checked['openavc']['origin'])
        sdk_root = Path(checked['openavc_oem']['origin'])
        if setup_sources is not None and not setup_sources:
            raise ValueError('Supply the inventoried setup source paths.')
        selection = None
        if setup_selection is not None:
            from openavc_oem.setup_inventory import selection_record
            selection = selection_record(setup_selection)
            candidate = setup_selection.candidate
            if (candidate.package_root.resolve(strict=True) != core_root
                    or candidate.app_root.resolve(strict=True) != core_root.parent
                    or os.path.normcase(str(candidate.server_python.absolute()))
                    != os.path.normcase(str(Path(sys.executable).absolute()))):
                raise ValueError('Run the census with the selected candidate Python and verified package/app roots.')
        with tempfile.TemporaryDirectory(prefix='openavc-setup-preflight-') as cache:
            child = (_CENSUS_CHILD if selection is not None else
                     'from openavc.drivers.setup_preflight import main; sys.exit(main())')
            command = [sys.executable, '-I', '-S', '-B', '-X', 'pycache_prefix=' + cache, '-c',
                       _PACKAGE_BOOTSTRAP + child, str(core_root), str(sdk_root)]
            if selection is not None:
                command.extend([str(core_root / 'drivers/setup-compatibility.json'), setup_resource_sha,
                                checked['openavc']['wheel_sha256']])
                # Canonical JSON is also the core assessment's selection hash.
                # ASCII escaping keeps text-mode pipes byte-identical on Windows.
                payload = json.dumps(selection, sort_keys=True, separators=(',', ':'), allow_nan=False)
                if len(payload.encode('utf-8')) > 262144:
                    raise ValueError('The firmware selection request exceeds its byte limit.')
            else:
                payload = None
                command.extend(['--resource', str(core_root / 'drivers/setup-compatibility.json'),
                                '--expect-resource', setup_resource_sha,
                                '--target-sha256', checked['openavc']['wheel_sha256']])
                for source in setup_sources:
                    command.extend(['--source', str(source)])
            result = _run_evaluator(command, payload)
        try:
            setup = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise ValueError('The checked core setup evaluator did not return a report.') from None
        if (result.returncode not in (0, 1) or type(setup) is not dict
                or setup.get('activation_authorized') is not False
                or setup.get('status') != (('assessed' if selection is not None else 'passed')
                                          if result.returncode == 0 else 'refused')):
            raise ValueError('The checked core setup evaluator did not return a valid disposition.')
        if selection is not None:
            sources = setup.get('sources', {})
            if (setup.get('schema') != 'sdk-server-setup-assessment/v1'
                    or setup.get('installation_complete') is not False
                    or setup.get('profile_roles_observed') is not True
                    or setup.get('selection') != selection
                    or setup.get('selection_sha256') != hashlib.sha256(payload.encode('utf-8')).hexdigest()
                    or type(sources) is not dict
                    or sources.get('installation_complete') is not False
                    or sources.get('activation_authorized') is not False
                    or sources.get('target_sha256') != checked['openavc']['wheel_sha256']
                    or sources.get('resource_sha256') != setup_resource_sha
                    or setup.get('evaluator') != {
                        'python': sys.executable,
                        'core_module': str(core_root / 'drivers/setup_preflight.py'),
                        'sdk_module': str(sdk_root / 'setup_inventory.py')}):
                raise ValueError('The candidate census report differs from the selected payload or request.')
            report.update(status=setup['status'], setup_inventory=setup,
                          scope='package_payloads_and_selected_server_census',
                          installation_complete=False, activation_authorized=False)
            return report
        report.update(status=setup['status'], setup_sources=setup,
                      scope='package_payloads_and_supplied_primary_sources',
                      activation_authorized=False)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path,
                        help='Extracted, trusted offline server bundle directory')
    parser.add_argument('--expect-core', required=True,
                        help='Complete core commit selected for this firmware build')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--setup-source', action='append', type=Path,
                           help='Explicit regular Python source in a protected installation snapshot; repeat per source')
    selection.add_argument('--setup-selection', type=Path,
                           help='Protected SDK server selection JSON emitted by the firmware update owner')
    args = parser.parse_args()
    try:
        selected = None
        if args.setup_selection is not None:
            from openavc_oem.setup_inventory import selection_from_payload
            if not args.setup_selection.is_file():
                raise ValueError('Supply a protected regular server selection file.')
            with args.setup_selection.open('rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('Supply a protected regular server selection file.')
                selected = selection_from_payload(stream.read(262145))
        result = check(args.bundle.resolve(strict=True), args.expect_core,
                       setup_sources=args.setup_source, setup_selection=selected)
    except (OSError, ValueError, KeyError, TypeError, BadZipFile, subprocess.TimeoutExpired) as error:
        result = {'status': 'refused', 'detail': str(error)}
        if args.setup_source is not None or args.setup_selection is not None:
            result['activation_authorized'] = False
        if args.setup_selection is not None:
            result['installation_complete'] = False
        print(json.dumps(result))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('passed', 'assessed') else 1


if __name__ == '__main__':
    sys.exit(main())
