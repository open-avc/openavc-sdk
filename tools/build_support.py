"""Shared build logging and optional persistent BuildKit cache for SDK builders."""
from collections import deque
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def show_failure(log_path, label):
    print(f'{label} failed. Last output from {log_path}:', file=sys.stderr)
    with log_path.open(encoding='utf-8', errors='replace') as log:
        excerpt = ''.join(deque(log, maxlen=80))[-16000:]
    print(excerpt, file=sys.stderr)


def run_logged(command, log_path, label):
    with log_path.open('w', encoding='utf-8') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        show_failure(log_path, label)
        result.check_returncode()


def build_image(docker, arguments, log_path, scope):
    """Reuse content-addressed layers; a failed build is never retried here.

    OPENAVC_BUILD_CACHE is a dedicated cache directory, optional outside CI.
    Each successful export replaces only this builder's own named cache.
    BuildKit still keys layers on their inputs. A cache is not acceptance evidence.
    """
    location = os.environ.get('OPENAVC_BUILD_CACHE')
    if not location:
        return run_logged(docker + ['build', '--progress=plain', *arguments], log_path, scope)
    if not re.fullmatch(r'[a-z0-9-]+', scope):
        raise ValueError('Invalid SDK build cache name.')
    root = Path(location).resolve()
    root.mkdir(parents=True, exist_ok=True)
    previous = root / scope
    if previous.is_symlink():
        raise ValueError('SDK build cache must not be a symbolic link.')
    fresh = Path(tempfile.mkdtemp(prefix=scope + '-', dir=root))
    options = ['buildx', 'build', '--load', '--progress=plain']
    if (previous / 'index.json').is_file():
        options += ['--cache-from', f'type=local,src={previous}']
    options += ['--cache-to', f'type=local,dest={fresh},mode=max']
    try:
        run_logged(docker + options + arguments, log_path, scope)
        if previous.exists():
            shutil.rmtree(previous)
        fresh.rename(previous)
    finally:
        if fresh.exists():
            shutil.rmtree(fresh)
