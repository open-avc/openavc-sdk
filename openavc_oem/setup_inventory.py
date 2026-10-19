"""Conditional source census for the SDK-managed Python server layout.

Selections are supplied by the firmware owner. Types and hashes bind what was
assessed; they do not prove where a running server imported code or authenticate
a firmware build. This API reads files only, starts no process and never grants
activation. Use it in the qualified candidate environment, with the actual old
and candidate selections retained by the owning firmware operation.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
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

    if type(selection) is not FirmwareSetupSelection:
        raise ValueError('Supply an explicit firmware setup selection.')
    _digest(selection.attempt_sha256)
    _digest(selection.recovery_selection_sha256)
    old, new = selection.installed, selection.candidate
    launches = {'installed': _launch(old), 'candidate': _launch(new)}
    if (type(selection.pending_imports) is not tuple or len(selection.pending_imports) > 32
            or any(not isinstance(path, Path) for path in selection.pending_imports)):
        raise ValueError('Supply the explicit bounded pending-import selection, including an empty tuple.')
    for path in selection.pending_imports:
        _path(path)
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
    facts = {'attempt_sha256': selection.attempt_sha256, 'launches': launches,
             'pending_imports': [str(path) for path in selection.pending_imports],
             'recovery_selection_sha256': selection.recovery_selection_sha256}
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
