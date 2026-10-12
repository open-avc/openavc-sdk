"""Compare completed resource observations without inferring hardware suitability."""
import hashlib
import json
import math
from pathlib import Path


METRICS = ('rss_bytes', 'cpu_percent', 'threads', 'descriptors')
HOST_FIELDS = ('system', 'release', 'machine', 'logical_cpus', 'memory_bytes')


def number(value, field, *, optional=False):
    if optional and value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid resource report value: ' + field)
    return value


def load(filename):
    path = Path(filename)
    with path.open('rb') as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('Resource reports must be at most 16 MiB.')
    report = json.loads(raw)
    if (type(report) is not dict or report.get('format') != 1
            or report.get('coverage') != 'selected_local_processes_and_observed_descendants'):
        raise ValueError('Select report.json files produced by profile-product.py.')
    if report.get('status') != 'complete':
        raise ValueError('Compare completed measurements. A failed or unfinished report is not a baseline.')
    if (report.get('cpu_percent_basis') != '100_percent_is_one_logical_cpu'
            or report.get('memory_basis') != 'sum_of_RSS_may_count_shared_pages_more_than_once'):
        raise ValueError('Resource measurement units differ or are missing.')
    if type(report.get('host')) is not dict or type(report.get('peak')) is not dict:
        raise ValueError('Resource report is missing host information or observed peaks.')
    context = report.get('context', {})
    if type(context) is not dict:
        raise ValueError('Invalid measurement context.')
    for field in ('label', 'workload'):
        if type(context.get(field, '')) is not str or len(context.get(field, '')) > 1024:
            raise ValueError('Invalid measurement ' + field)
    artifacts = context.get('artifacts', [])
    if type(artifacts) is not list:
        raise ValueError('Invalid artifact list.')
    selected_artifacts = []
    for artifact in artifacts:
        if (type(artifact) is not dict or type(artifact.get('name')) is not str
                or type(artifact.get('sha256')) is not str or len(artifact['sha256']) != 64
                or any(char not in '0123456789abcdef' for char in artifact['sha256'])):
            raise ValueError('Invalid artifact identity.')
        selected_artifacts.append({'name': artifact['name'], 'sha256': artifact['sha256'],
                                   'bytes': number(artifact.get('bytes'), 'artifact.bytes')})
    initial = report.get('initial_storage')
    last = report.get('last', {}).get('storage') if type(report.get('last')) is dict else None
    storage_growth = None
    if (type(initial) is dict and type(last) is dict
            and initial.get('complete') is True and last.get('complete') is True):
        storage_growth = number(last.get('bytes'), 'last.storage.bytes') - number(initial.get('bytes'), 'initial_storage.bytes')
    return {'report_sha256': hashlib.sha256(raw).hexdigest(),
            'label': context.get('label', ''), 'workload': context.get('workload', ''),
            'artifacts': selected_artifacts,
            'host': {key: report['host'].get(key) for key in HOST_FIELDS},
            'elapsed_seconds': number(report.get('elapsed_seconds'), 'elapsed_seconds'),
            'duration_requested_seconds': number(report.get('duration_requested_seconds'), 'duration_requested_seconds'),
            'interval_seconds': number(report.get('interval_seconds'), 'interval_seconds'),
            'samples': number(report.get('samples'), 'samples'),
            'observed_peak': {key: number(report['peak'].get(key), 'peak.' + key, optional=True) for key in METRICS},
            'storage_growth_bytes': storage_growth}


def compare(baseline_path, candidate_path):
    baseline, candidate = load(baseline_path), load(candidate_path)
    notes = []
    for field in HOST_FIELDS:
        if baseline['host'][field] != candidate['host'][field]:
            notes.append('Reported host ' + field + ' differs.')
    for field in ('duration_requested_seconds', 'interval_seconds'):
        if baseline[field] != candidate[field]:
            notes.append('Measurement ' + field + ' differs.')
    if not baseline['workload'] or not candidate['workload']:
        notes.append('A workload description was not supplied for both measurements.')
    elif baseline['workload'] != candidate['workload']:
        notes.append('The declared workloads differ.')
    if not baseline['artifacts'] or not candidate['artifacts']:
        notes.append('Artifact identities were not supplied for both measurements.')
    if baseline['report_sha256'] == candidate['report_sha256']:
        notes.append('Both inputs contain the same report; this is not an independent comparison.')
    metrics = {}
    for name in (*METRICS, 'storage_growth_bytes'):
        before = baseline[name] if name == 'storage_growth_bytes' else baseline['observed_peak'][name]
        after = candidate[name] if name == 'storage_growth_bytes' else candidate['observed_peak'][name]
        delta = after - before if before is not None and after is not None else None
        metrics[name] = {'baseline': before, 'candidate': after, 'difference': delta,
                         'change_percent': 100 * delta / before if delta is not None and before > 0 else None}
    return {'format': 1, 'coverage': 'resource_observation_comparison',
            'baseline': baseline, 'candidate': candidate, 'metrics': metrics, 'notes': notes,
            'interpretation': 'Observed peaks and storage growth. Matching metadata does not prove equal hardware, workload or AV quality.',
            'cpu_percent_basis': '100_percent_is_one_logical_cpu',
            'memory_basis': 'sum_of_RSS_may_count_shared_pages_more_than_once'}
