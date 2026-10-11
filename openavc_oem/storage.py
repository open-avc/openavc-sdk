"""Atomic product settings and bounded local diagnostics."""
import json
import os
from pathlib import Path

def sync_parent(path):
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def save_json(path, value):
    path = Path(path)
    pending = path.with_suffix(path.suffix + '.pending')
    with pending.open('w') as stream:
        os.chmod(pending, 0o600)
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)
    sync_parent(path)

class Journal:
    """Retain up to three 1 MiB segments; synchronize every written event."""

    def __init__(self, path, limit=1024 * 1024):
        self.path, self.limit = Path(path), limit

    def append(self, value):
        data = (json.dumps(value, separators=(',', ':')) + '\n').encode()
        if len(data) > self.limit:
            raise ValueError('Diagnostic event exceeds segment size')
        size = self.path.stat().st_size if self.path.exists() else 0
        if size + len(data) > self.limit:
            older = self.path.with_name(self.path.name + '.1')
            if older.exists():
                older.replace(self.path.with_name(self.path.name + '.2'))
            self.path.replace(older)
        with self.path.open('ab') as stream:
            os.chmod(self.path, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        sync_parent(self.path)
