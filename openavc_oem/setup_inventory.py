"""Conditional source census for the SDK-managed Python server layout.

Selections are supplied by the firmware owner. Types and hashes bind what was
assessed; they do not prove where a running server imported code or authenticate
a firmware build. This API reads files only, starts no process and never grants
activation. Use it in the qualified candidate environment, with the actual old
and candidate selections retained by the owning firmware operation.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import re


@dataclass(frozen=True)
class ServerSelection:
    """Allowlisted launch facts; never configuration bytes or environment secrets."""

    config_sha256: str
    effective_launch_sha256: str
    server_python: Path
    product_data: Path
    package_root: Path
    app_root: Path


@dataclass(frozen=True)
class FirmwareSetupSelection:
    attempt_sha256: str
    installed: ServerSelection
    candidate: ServerSelection
    pending_imports: tuple[Path, ...]
    recovery_selection_sha256: str


def _digest(value):
    if type(value) is not str or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Supply exact firmware-selection SHA-256 identities.')
    return value


def _path(value):
    if not isinstance(value, Path) or not value.is_absolute() or len(str(value)) > 4096:
        raise ValueError('Supply absolute paths from the selected Python server launch.')
    return value


def _launch(selection):
    if type(selection) is not ServerSelection:
        raise ValueError('This inventory requires an explicit Python server selection.')
    _digest(selection.config_sha256)
    _digest(selection.effective_launch_sha256)
    for value in (selection.server_python, selection.product_data,
                  selection.package_root, selection.app_root):
        _path(value)
    return {key: str(value) if isinstance(value, Path) else value
            for key, value in asdict(selection).items()}


def selection_record(selection: FirmwareSetupSelection) -> dict:
    """Serialize only closed selection facts, never a capture or credential config."""
    if type(selection) is not FirmwareSetupSelection:
        raise ValueError('Supply an explicit firmware setup selection.')
    _digest(selection.attempt_sha256)
    _digest(selection.recovery_selection_sha256)
    launches = {'installed': _launch(selection.installed), 'candidate': _launch(selection.candidate)}
    if (type(selection.pending_imports) is not tuple or len(selection.pending_imports) > 32
            or any(not isinstance(path, Path) for path in selection.pending_imports)):
        raise ValueError('Supply the explicit bounded pending-import selection, including an empty tuple.')
    for path in selection.pending_imports:
        _path(path)
    return {'schema': 'sdk-server-selection/v1',
            'attempt_sha256': selection.attempt_sha256, 'launches': launches,
            'pending_imports': [str(path) for path in selection.pending_imports],
            'recovery_selection_sha256': selection.recovery_selection_sha256}


def selection_from_record(record: dict) -> FirmwareSetupSelection:
    """Decode the bounded internal candidate-child request, not source observations."""
    if (type(record) is not dict or set(record) != {
            'schema', 'attempt_sha256', 'launches', 'pending_imports', 'recovery_selection_sha256'}
            or record['schema'] != 'sdk-server-selection/v1'
            or type(record['launches']) is not dict
            or set(record['launches']) != {'installed', 'candidate'}
            or type(record['pending_imports']) is not list or len(record['pending_imports']) > 32
            or any(type(path) is not str for path in record['pending_imports'])):
        raise ValueError('The firmware server selection is incomplete or unsupported.')
    launches = []
    for name in ('installed', 'candidate'):
        value = record['launches'][name]
        if (type(value) is not dict or set(value) != {
                'config_sha256', 'effective_launch_sha256', 'server_python',
                'product_data', 'package_root', 'app_root'}
                or any(type(item) is not str for item in value.values())):
            raise ValueError('The firmware launch selection is incomplete or unsupported.')
        launches.append(ServerSelection(value['config_sha256'], value['effective_launch_sha256'],
                                        *(Path(value[key]) for key in (
                                            'server_python', 'product_data', 'package_root', 'app_root'))))
    result = FirmwareSetupSelection(record['attempt_sha256'], *launches,
                                    tuple(Path(path) for path in record['pending_imports']),
                                    record['recovery_selection_sha256'])
    selection_record(result)
    return result


def selection_from_payload(payload: bytes) -> FirmwareSetupSelection:
    """Read a closed, bounded UTF-8 request from the verified candidate parent."""
    if type(payload) is not bytes or len(payload) > 262144:
        raise ValueError('The firmware selection request exceeds its byte limit.')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('The firmware selection request repeats a field.')
            result[key] = value
        return result

    try:
        record = json.loads(payload.decode('utf-8'), object_pairs_hook=unique)
    except RecursionError:
        raise ValueError('The firmware selection request is nested too deeply.') from None
    return selection_from_record(record)


def assess_server_selection(selection: FirmwareSetupSelection, *, resource: Path,
                            expected_resource_sha256: str, target_sha256: str) -> dict:
    """Derive every fixed role and assess captured bytes without installation approval.

    An empty source set is meaningful only for this observed selection. Actual
    launch/package association, recovery, clients and operation exclusion remain
    separate obligations even when all role observations and sources pass.
    """
    from openavc.drivers.setup_compatibility import declaration_digest
    from openavc.drivers.setup_inventory import capture_source_inputs
    from openavc.drivers.setup_preflight import assess_capture

    facts = selection_record(selection)
    old, new = selection.installed, selection.candidate
    if not old.product_data.samefile(new.product_data):
        raise ValueError('The selected server data directory changes; qualify its migration before replacement.')
    data_identity = old.product_data.stat()
    # Observe both spellings. Aliased roles remain distinct and share the same
    # capture limits; no second scan/evaluator or caller-selected root subset.
    capture = capture_source_inputs(
        primary_roots={
            'installed_builtin_python': old.package_root / 'drivers/definitions',
            'candidate_builtin_python': new.package_root / 'drivers/definitions',
        }, library_roots={},
        bundles={f'pending_import_{index}': path for index, path in enumerate(selection.pending_imports)},
        optional_primary_roots={
            'installed_persistent_python': old.product_data / 'server/driver_repo',
            'candidate_persistent_python': new.product_data / 'server/driver_repo',
            'retained_legacy_python': old.app_root / 'driver_repo',
            'candidate_legacy_python': new.app_root / 'driver_repo',
        }, optional_library_roots={
            'installed_saved_library': old.product_data / 'server/saved_projects',
            'candidate_saved_library': new.product_data / 'server/saved_projects',
        }, template_roots={
            'installed_templates': old.package_root / 'templates',
            'candidate_templates': new.package_root / 'templates',
        },
    )
    after = old.product_data.stat()
    if ((data_identity.st_dev, data_identity.st_ino) != (after.st_dev, after.st_ino)
            or not old.product_data.samefile(new.product_data)):
        raise ValueError('The selected server data directory changed during inventory.')
    assessment = assess_capture(capture, resource=resource,
                                expected_resource_sha256=expected_resource_sha256,
                                target_sha256=target_sha256)
    return {
        'schema': 'sdk-server-setup-assessment/v1', 'profile': 'sdk-python-server/v1',
        'scope': 'supplied_sdk_server_selection',
        'status': 'refused' if assessment['source_eligibility'] == 'refused' else 'assessed',
        'profile_roles_observed': True, 'installation_complete': False, 'activation_authorized': False,
        'selection': facts, 'selection_sha256': declaration_digest(facts), 'sources': assessment,
        'unproven': ['installed_launch_association', 'candidate_launch_association',
                     'package_authenticity_and_dependencies', 'client_compatibility',
                     'recovery_destination_mapping', 'writer_exclusion_and_cutover',
                     'first_upgrade_entrypoint'],
    }
