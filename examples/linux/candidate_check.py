"""Observe a candidate census while this disposable example's old server runs.

This adapter owns no stop, swap or recovery operation. Its launch observations
apply only to processes created by check.py, not an arbitrary installed service.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from zipfile import ZipFile

import psutil

from openavc_oem.setup_inventory import FirmwareSetupSelection, ServerSelection, selection_record


def sha(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


class CandidateCheck:
    """A conditional assessment and refusal-preservation exercise, never cutover."""

    def __init__(self, args, evidence, config_path, environment):
        self.evidence = evidence / 'candidate-assessment'
        self.evidence.mkdir(mode=0o700)
        self.config_path = config_path
        self.config_bytes = config_path.read_bytes()
        self.config = json.loads(self.config_bytes)
        self.data = Path(self.config['data_dir'])
        self.old_python = str(args.python)
        self.new_python = os.path.abspath(args.candidate_python)
        self.transition = args.server_transition
        self.old_bundle = args.installed_bundle.absolute()
        self.new_bundle = args.candidate_bundle.absolute()
        # This is a fresh, explicitly instrumented launch, not discovery of an
        # arbitrary old interpreter. No ambient Python path overrides are used.
        if any(key in environment for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE',
                                              'PYTHONSTARTUP', 'PYTHONINSPECT', 'PYTHONPLATLIBDIR',
                                              'PYTHONPYCACHEPREFIX')):
            raise ValueError('Run the candidate example without ambient Python overrides.')
        self.environment = {**environment, 'PYTHONVERBOSE': '1',
                            'PYTHONPYCACHEPREFIX': str(self.evidence / 'unused-bytecode')}
        self.packages = {}
        self.observations = 0
        self.candidate_config = {**self.config, 'server_python': self.new_python}
        self.candidate_config_path = self.evidence / 'candidate-config.json'

    def write_candidate_config(self):
        """Retain a separate candidate config; never replace the old config."""
        self.candidate_config_path.write_bytes(encoded(self.candidate_config))
        self.candidate_config_path.chmod(0o600)
        if os.getuid() == 0:
            identity = self.data.stat()
            os.chown(self.evidence, identity.st_uid, identity.st_gid)
            os.chown(self.candidate_config_path, identity.st_uid, identity.st_gid)
        return self.candidate_config_path

    def verify_packages(self):
        for name, python, bundle in (('installed', self.old_python, self.old_bundle),
                                     ('candidate', self.new_python, self.new_bundle)):
            core = json.loads((bundle / 'bundle.json').read_text())['core']['core_head']
            command = [python, '-I', '-B', '-m', 'openavc_oem.preflight',
                       '--bundle', str(bundle), '--expect-core', core]
            result = self.invoke(name + '-payload', command)
            if result.get('status') != 'passed' or result.get('core_commit') != core:
                raise ValueError('The selected installed server payload was refused. Read candidate-assessment.')
            self.packages[name] = result
        self.recovery = {'policy': ('same_format_return_keep_current_data' if self.transition
                                    else 'assessment_only_keep_current_server'),
                         'project_restore_planned': False,
                         'config_sha256': sha(self.config_bytes),
                         'python': self.old_python,
                         'bundle_sha256': sha((self.old_bundle / 'bundle.json').read_bytes())}

    def invoke(self, name, command):
        # Only the selected trusted bundle's CLI runs here. Its evaluator has
        # finite output storage; this outer deadline also covers package checks.
        # -B prevents writes but still reads existing caches. A fresh prefix
        # applies to the CLI itself, as well as its separately isolated child.
        with tempfile.TemporaryDirectory(prefix='cli-cache-', dir=self.evidence) as cache:
            command = [command[0], '-X', 'pycache_prefix=' + cache, *command[1:]]
            (self.evidence / (name + '-command.json')).write_bytes(encoded(command))
            try:
                result = subprocess.run(command, cwd=self.data, env={
                    key: value for key, value in self.environment.items() if not key.startswith('PYTHON')},
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=150)
            except subprocess.TimeoutExpired as error:
                (self.evidence / (name + '.stdout')).write_bytes(error.stdout or b'')
                (self.evidence / (name + '.stderr')).write_bytes(error.stderr or b'')
                raise ValueError('Candidate preflight timed out. Keep the current server running.') from None
        (self.evidence / (name + '.stdout')).write_bytes(result.stdout)
        (self.evidence / (name + '.stderr')).write_bytes(result.stderr)
        try:
            report = json.loads(result.stdout)
        except (ValueError, RecursionError):
            raise ValueError('Candidate preflight returned no readable report. Keep the current server running.') from None
        if (type(report) is not dict or result.returncode not in (0, 1)
                or (result.returncode == 1) != (report.get('status') == 'refused')):
            raise ValueError('Candidate preflight returned an invalid disposition. Keep the current server running.')
        return report

    def before_manager_launch(self, log_path):
        self.journal = self.data / 'logs/recovery.jsonl'
        self.journal_offset = self.journal.stat().st_size
        self.manager_log = log_path

    def observe(self, manager, hardware, management, *, role='installed'):
        if manager.poll() is not None or hardware.poll() is not None:
            raise ValueError('The current example processes stopped before candidate assessment.')
        python = self.old_python if role == 'installed' else self.new_python
        config_path = self.config_path if role == 'installed' else self.candidate_config_path
        config_bytes = self.config_bytes if role == 'installed' else encoded(self.candidate_config)
        bundle = self.old_bundle if role == 'installed' else self.new_bundle
        manager_process = psutil.Process(manager.pid)
        manager_command = [python, '-m', 'openavc_oem', '--config', str(config_path)]
        manager_environment = manager_process.environ()
        if (manager_process.cmdline() != manager_command or manager_process.cwd() != str(self.data)
                or manager_environment != self.environment
                or not any(item.fd == 2 and item.path == str(self.manager_log)
                           for item in manager_process.open_files())):
            raise ValueError('The current Manager launch differs from the retained example selection.')
        children = [child for child in manager_process.children()
                    if child.cmdline() == [python, '-m', 'openavc_oem.server']]
        if len(children) != 1:
            raise ValueError('The example cannot identify its running server. Keep the current installation.')
        core = children[0]
        state = management()
        diagnostics = state['process_logs']
        if (not state.get('server_responsive') or diagnostics['error']
                or diagnostics['dropped_bytes'] or diagnostics['queued_chunks']):
            raise ValueError('The current server or its import observations are incomplete.')
        journal = self.journal.read_bytes()[self.journal_offset:]
        launches = [json.loads(line) for line in journal.splitlines()
                    if json.loads(line).get('event') == 'launched']
        if len(launches) != 1 or launches[0]['pid'] != core.pid or launches[0]['generation'] != 1:
            raise ValueError('The current server generation differs from the observed example launch.')
        environment = core.environ()
        if (core.cwd() != str(self.data)
                or environment.get('OPENAVC_DATA_DIR') != str(self.data / 'server')
                or environment.get('OPENAVC_PROJECT') != str(self.data / 'server/project.avc')
                or environment.get('PYTHONVERBOSE') != '1'
                or environment.get('PYTHONDONTWRITEBYTECODE') != '1'
                or environment.get('PYTHONPYCACHEPREFIX') != self.environment['PYTHONPYCACHEPREFIX']
                or config_path.read_bytes() != config_bytes):
            raise ValueError('The current server launch differs from the retained example selection.')
        packages = self.packages[role]['packages']
        # First-launch verbose imports plus a fresh unused cache prefix give
        # observed source paths, tied to this manager's direct child and logs.
        log_paths = [self.data / 'logs' / ('core-0.log' + suffix) for suffix in ('.2', '.1', '')]
        logs = b''.join(path.read_bytes() for path in log_paths if path.exists())
        lines = logs.decode('utf-8', errors='strict').splitlines()
        imported = [Path(packages['openavc']['origin']) / '__init__.py',
                    Path(packages['openavc']['origin']) / 'main.py',
                    Path(packages['openavc']['origin']) / 'system_config.py',
                    Path(packages['openavc_oem']['origin']) / '__init__.py',
                    Path(packages['openavc_oem']['origin']) / 'server.py']
        if any('# code object from ' + str(path) not in lines for path in imported):
            raise ValueError('The server import observations are incomplete. Keep the current installation.')
        manager_imported = [Path(packages['openavc_oem']['origin']) / name
                            for name in ('__init__.py', '__main__.py', 'manager.py')]
        manager_logs = self.manager_log.read_bytes()
        manager_lines = manager_logs.decode('utf-8', errors='strict').splitlines()
        if any('# code object from ' + str(path) not in manager_lines for path in manager_imported):
            raise ValueError('The Manager import observations are incomplete. Keep the current installation.')
        (self.evidence / f'{role}-server-imports-{self.observations}.log').write_bytes(logs)
        (self.evidence / f'{role}-manager-imports-{self.observations}.log').write_bytes(manager_logs)
        self.observations += 1
        for name, package in packages.items():
            root = Path(package['origin'])
            with ZipFile(bundle / 'wheels' / package['wheel']) as wheel:
                for path in [*imported, *manager_imported]:
                    if path.is_relative_to(root):
                        if path.read_bytes() != wheel.read(name + '/' + path.relative_to(root).as_posix()):
                            raise ValueError('Observed server source differs from its retained bundle.')
        manager_launch = {'command': manager_process.cmdline(), 'cwd': manager_process.cwd(),
                          'environment_sha256': sha(encoded(manager_environment)),
                          'package_observations': {str(path): sha(path.read_bytes()) for path in manager_imported}}
        launch = {'command': core.cmdline(), 'cwd': core.cwd(),
                  'environment_sha256': sha(encoded(environment)),
                  'package_observations': {str(path): sha(path.read_bytes()) for path in imported}}
        return {'manager_pid': manager.pid, 'hardware_pid': hardware.pid,
                'manager_created': manager_process.create_time(), 'manager_launch': manager_launch,
                'server_pid': core.pid, 'server_created': core.create_time(),
                'launch': launch, 'effective_launch_sha256': sha(encoded({'manager': manager_launch, 'server': launch}))}

    def selection(self, observation):
        old_root = Path(self.packages['installed']['packages']['openavc']['origin'])
        new_root = Path(self.packages['candidate']['packages']['openavc']['origin'])
        # This binds a planned candidate launch, not a claim that it ran. Future
        # tokens/listener handles and operation exclusion remain the owner's job.
        planned = {'command': [self.new_python, '-m', 'openavc_oem.server'],
                   'cwd': str(self.data), 'config_sha256': sha(self.candidate_config_path.read_bytes()),
                   'package_root': str(new_root), 'state': 'not_started'}
        attempt = {'old': observation, 'planned_candidate': planned, 'recovery': self.recovery,
                   'candidate_bundle_sha256': sha((self.new_bundle / 'bundle.json').read_bytes())}
        (self.evidence / 'attempt.json').write_bytes(encoded(attempt))
        return FirmwareSetupSelection(sha(encoded(attempt)),
            ServerSelection(sha(self.config_bytes), observation['effective_launch_sha256'],
                            Path(self.old_python), self.data, old_root, old_root.parent),
            ServerSelection(sha(self.candidate_config_path.read_bytes()), sha(encoded(planned)),
                            Path(self.new_python), self.data, new_root, new_root.parent),
            (), sha(encoded(self.recovery)))

    def process_ids(self, manager, hardware):
        processes = [psutil.Process(manager.pid), psutil.Process(hardware.pid)]
        children = [child for child in processes[0].children()
                    if child.cmdline() == [self.old_python, '-m', 'openavc_oem.server']]
        if len(children) != 1 or manager.poll() is not None or hardware.poll() is not None:
            raise ValueError('The owned example server processes are unavailable.')
        return [{'pid': process.pid, 'created': process.create_time(), 'command': process.cmdline()}
                for process in [*processes, children[0]]]

    def exercise(self, manager, hardware, management, *, refuse_unknown=True):
        processes = self.process_ids(manager, hardware)
        ready = management().get('server_responsive') is True
        if not ready:
            raise ValueError('The old server is not responsive before candidate assessment.')
        try:
            self.write_candidate_config()
            if self.candidate_config_path.read_bytes() != encoded(self.candidate_config):
                raise ValueError('The prepared candidate configuration differs from its selection.')
            self.verify_packages()
            return self.assess_sources(manager, hardware, management, refuse_unknown=refuse_unknown)
        finally:
            # check.py tears down its disposable fixture after this method.
            # Preserve this observation even when the CLI crashes or refuses
            # before a complete census report exists.
            observation = {'before': processes, 'scope': 'before_fixture_teardown',
                           'stop_or_swap_called': False, 'candidate_started': False}
            try:
                after = self.process_ids(manager, hardware)
                observation.update(after=after, processes_unchanged=after == processes,
                                   server_responsive=management().get('server_responsive') is True)
            except (OSError, ValueError, psutil.Error) as error:
                observation.update(processes_unchanged=False, detail=type(error).__name__)
            (self.evidence / 'pre-teardown-preservation.json').write_bytes(encoded(observation))
            if not observation.get('processes_unchanged') or not observation.get('server_responsive'):
                raise ValueError('The original server did not remain available during candidate assessment.')

    def assess_sources(self, manager, hardware, management, *, refuse_unknown=True):
        before = self.observe(manager, hardware, management)
        if not management().get('server_responsive'):
            raise ValueError('The old server is not responsive before candidate assessment.')
        selection = selection_record(self.selection(before))
        selection_path = self.evidence / 'selection.json'
        selection_path.write_bytes(encoded(selection))
        selection_path.chmod(0o600)
        core = self.packages['candidate']['core_commit']
        command = [self.new_python, '-I', '-B', '-m', 'openavc_oem.preflight',
                   '--bundle', str(self.new_bundle), '--expect-core', core,
                   '--setup-selection', str(selection_path)]

        def assess(name, expected):
            result = self.invoke(name, command)
            census = result.get('setup_inventory', {})
            if (result.get('status') != expected or result.get('activation_authorized') is not False
                    or result.get('installation_complete') is not False
                    or result.get('core_commit') != core
                    or result.get('packages') != self.packages['candidate']['packages']
                    or census.get('selection') != selection
                    or census.get('selection_sha256') != sha(encoded(selection))):
                raise ValueError('The candidate census did not match this attempt. Read candidate-assessment.')
            after = self.observe(manager, hardware, management)
            if after != before or not management().get('server_responsive'):
                raise ValueError('The running server changed during candidate assessment.')
            return {'status': expected, 'processes_unchanged': True, 'server_responsive': True,
                    'activation_authorized': False, 'installation_complete': False}

        baseline = assess('baseline', 'assessed')
        if not refuse_unknown:
            return {'scope': 'owned_example_pre_stop_transition_assessment',
                    'baseline': baseline, 'old_processes': before,
                    'activation_authorized': False, 'installation_complete': False}
        # A new inactive library file challenges reuse of the first assessment.
        # It is retained on refusal, never installed or deleted to force a pass.
        library = self.data / 'server/saved_projects/candidate-check-unknown'
        library.mkdir(parents=True, exist_ok=False)
        refused_source = library / 'bundle.zip'
        with ZipFile(refused_source, 'x') as archive:
            archive.writestr('drivers/acme_candidate_unknown.py',
                             'raise RuntimeError("inactive candidate check source must not run")\n')
        if os.getuid() == 0:
            identity = self.data.stat()
            os.chown(library.parent, identity.st_uid, identity.st_gid)
            os.chown(library, identity.st_uid, identity.st_gid)
            os.chown(refused_source, identity.st_uid, identity.st_gid)
        original = refused_source.read_bytes()
        private = {str(path): sha(path.read_bytes()) for path in self.config_path.parent.rglob('*')
                   if path.is_file()}
        saved_paths = [self.data / name for name in ('management.json', 'server/project.avc',
                                                    'server/system.json', 'server/state.json')]
        saved = {path: path.read_bytes() if path.exists() else None for path in saved_paths}
        refused = assess('changed-source', 'refused')
        if (refused_source.read_bytes() != original
                or any((path.read_bytes() if path.exists() else None) != data for path, data in saved.items())
                or any(sha(Path(path).read_bytes()) != digest for path, digest in private.items())):
            raise ValueError('Selected source, project or private configuration changed during refusal.')
        return {'scope': 'owned_example_pre_stop_assessment', 'baseline': baseline, 'changed_source': refused,
                'old_processes': before, 'unknown_source_sha256': sha(original),
                'source_and_saved_configuration_unchanged': True,
                'candidate_started': False, 'update_attempt_stopped_or_swapped': False,
                'later_process_stop': 'explicit_disposable_fixture_teardown',
                'activation_authorized': False, 'installation_complete': False}
