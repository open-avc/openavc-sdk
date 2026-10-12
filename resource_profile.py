"""Measure selected local processes without collecting arguments or environment."""
import json
import hashlib
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import platform
import stat
import time

import psutil


def storage(directory):
    """Apparent regular-file size, without following links or reading file data."""
    size = files = 0
    complete = True
    def failed(error):
        nonlocal complete
        complete = False
    for root, directories, names in os.walk(directory, followlinks=False, onerror=failed):
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        for name in names:
            if files >= 100000:
                return {'bytes': size, 'files': files, 'complete': False}
            path = Path(root) / name
            try:
                if path.is_file() and not path.is_symlink():
                    size += path.stat().st_size
                    files += 1
            except OSError:
                complete = False
    return {'bytes': size, 'files': files, 'complete': complete}


def artifact_identity(filename):
    path = Path(filename)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Select regular files for --artifact.')
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Select regular files for --artifact.')
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('An artifact changed while it was being read. Select a stable firmware file.')
    return {'name': path.name, 'bytes': after.st_size, 'sha256': digest}


def profile(pids, output, duration=60, interval=1, data_directory=None, *, label='', workload='', artifacts=()):
    if not all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in (duration, interval)):
        raise ValueError('Duration and interval must be positive finite seconds.')
    if duration / interval > 1000000:
        raise ValueError('Choose an interval that produces at most one million samples.')
    if not pids or any(type(pid) is not int or pid <= 0 for pid in pids):
        raise ValueError('Select one or more local process IDs with --pid.')
    if data_directory is not None and not Path(data_directory).is_dir():
        raise ValueError('The selected data directory does not exist.')
    if any(type(value) is not str or len(value) > 1024 for value in (label, workload)):
        raise ValueError('Use at most 1024 characters for each measurement label and workload.')
    context = {'label': label, 'workload': workload,
               'artifacts': [artifact_identity(path) for path in artifacts],
               'artifact_observation': 'provided_files_before_measurement'}
    roots = {pid: psutil.Process(pid) for pid in set(pids)}
    identities = {pid: process.create_time() for pid, process in roots.items()}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    started = time.monotonic()
    report = {'format': 1, 'status': 'running', 'duration_requested_seconds': duration,
              'started_at': datetime.now(timezone.utc).isoformat(),
              'interval_seconds': interval, 'root_processes': identities, 'samples': 0,
              'context': context,
              'host': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine(),
                       'logical_cpus': psutil.cpu_count(), 'memory_bytes': psutil.virtual_memory().total},
              'peak': {}, 'coverage': 'selected_local_processes_and_observed_descendants',
              'cpu_percent_basis': '100_percent_is_one_logical_cpu',
              'memory_basis': 'sum_of_RSS_may_count_shared_pages_more_than_once'}
    previous = {}
    initial_storage = storage(data_directory) if data_directory else None
    report['initial_storage'] = initial_storage
    try:
        with (output / 'samples.jsonl').open('x') as stream:
            while True:
                now = time.monotonic()
                processes = {}
                for pid, root in roots.items():
                    if not root.is_running() or root.status() == psutil.STATUS_ZOMBIE or root.create_time() != identities[pid]:
                        raise ValueError('A selected process exited. Inspect its supervisor and start a new measurement.')
                    processes[pid] = root
                    processes.update({child.pid: child for child in root.children(recursive=True)})
                totals = {'rss_bytes': 0, 'cpu_percent': 0., 'threads': 0, 'descriptors': 0}
                rows = []
                for pid, process in sorted(processes.items()):
                    try:
                        with process.oneshot():
                            created = process.create_time()
                            cpu = process.cpu_times()
                            used = cpu.user + cpu.system
                            key = (pid, created)
                            earlier = previous.get(key)
                            percent = max(0., 100 * (used - earlier[1]) / (now - earlier[0])) if earlier else None
                            previous[key] = now, used
                            row = {'pid': pid, 'created': created, 'rss_bytes': process.memory_info().rss,
                                   'cpu_percent': percent, 'threads': process.num_threads()}
                            descriptor_method = getattr(process, 'num_fds', None) or getattr(process, 'num_handles', None)
                            row['descriptors'] = descriptor_method() if descriptor_method else None
                    except psutil.NoSuchProcess:
                        if pid in roots:
                            raise ValueError('A selected process exited during measurement.') from None
                        continue
                    # Permission errors invalidate the measurement instead of producing a false zero.
                    rows.append(row)
                    for key in totals:
                        if row[key] is None:
                            totals[key] = None
                        elif totals[key] is not None:
                            totals[key] += row[key]
                sample = {'elapsed_seconds': time.monotonic() - started, 'processes': rows, 'total': totals}
                observed = {(row['pid'], row['created']) for row in rows}
                previous = {key: value for key, value in previous.items() if key in observed}
                if data_directory:
                    sample['storage'] = storage(data_directory)
                    sample['disk_free_bytes'] = psutil.disk_usage(str(data_directory)).free
                stream.write(json.dumps(sample, allow_nan=False) + '\n')
                stream.flush()
                report['samples'] += 1
                report['last'] = sample
                for key, value in totals.items():
                    if value is not None:
                        report['peak'][key] = max(value, report['peak'].get(key, value))
                if sample['elapsed_seconds'] >= duration:
                    break
                time.sleep(min(interval, max(0, duration - (time.monotonic() - started))))
            os.fsync(stream.fileno())
        report['status'] = 'complete'
    except BaseException as error:
        report.update(status='failed', error=str(error) if isinstance(error, ValueError)
                      else 'Measurement interrupted or process metrics unavailable. Check process permissions and local storage.')
        raise
    finally:
        report['elapsed_seconds'] = time.monotonic() - started
        with (output / 'report.json').open('w') as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    return report
