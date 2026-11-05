"""Fetch exact Debian archives selected by the builders' signed snapshot indexes.

Only package bytes may come from the ordinary Debian mirror. Index acquisition
stays with apt-get update and the dated sources file. APT supplies each archive's
SHA-256; apt-helper and this tool both check it before the archive enters APT's
cache or a cross-build sysroot. Missing mirror files fall back to the original
snapshot URI, never to another package version.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

CONFIG = Path(__file__).with_name('apt-retries.conf')
HELPER = '/usr/lib/apt/apt-helper'
SNAPSHOT_PACKAGE = re.compile(
    r'https://snapshot\.debian\.org/archive/(debian(?:-security)?)/'
    r'[0-9]{8}T[0-9]{6}Z/(pool/[^?#]+\.deb)')


def mirror_uri(uri):
    match = SNAPSHOT_PACKAGE.fullmatch(uri)
    if match is None:
        raise ValueError(f'Expected a dated Debian package URI, received {uri!r}')
    return f'https://deb.debian.org/{match[1]}/{match[2]}'


def package_uris(output):
    packages = []
    names = set()
    for line in output.splitlines():
        if not line.startswith("'"):
            continue  # APT also prints its dependency-resolution summary.
        fields = shlex.split(line)
        if len(fields) != 4:
            raise ValueError(f'Invalid APT package record: {line}')
        uri, name, size, digest = fields
        mirror_uri(uri)
        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+%_~:-]*\.deb', name)
                or name in names or not size.isdecimal() or int(size) <= 0
                or not re.fullmatch(r'SHA256:[0-9a-f]{64}', digest)):
            raise ValueError(f'Invalid APT package name, size or SHA-256: {line}')
        names.add(name)
        packages.append({'uri': uri, 'file': name, 'size': int(size), 'sha256': digest[7:]})
    return packages


def matches(path, package):
    if not path.is_file() or path.stat().st_size != package['size']:
        return False
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest() == package['sha256']


def fetch_packages(packages, destination):
    destination.mkdir(parents=True, exist_ok=True)
    pending = list(packages)
    records = []
    for use_mirror in (True, False):
        if not pending:
            break
        # Separate attempts cannot resume a rejected mirror response.
        with tempfile.TemporaryDirectory(prefix='sdk-debs-') as temporary:
            folder = Path(temporary)
            if os.geteuid() == 0:
                shutil.chown(folder, user='_apt')
            command = [HELPER, '-c', str(CONFIG), 'download-file']
            for package in pending:
                uri = mirror_uri(package['uri']) if use_mirror else package['uri']
                command.extend([uri, str(folder / package['file']), 'SHA256:' + package['sha256']])
            subprocess.run(command, check=False)
            missing = []
            for package in pending:
                downloaded = folder / package['file']
                if not matches(downloaded, package):
                    missing.append(package)
                    continue
                shutil.move(downloaded, destination / package['file'])
                records.append({**package, 'download_uri': (
                    mirror_uri(package['uri']) if use_mirror else package['uri'])})
            pending = missing
    if pending:
        raise RuntimeError('Could not obtain verified Debian archives: '
                           + ', '.join(package['file'] for package in pending))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('action', choices=['install', 'download'])
    parser.add_argument('packages', nargs='+')
    args = parser.parse_args()
    # Builders run apt-get update successfully before invoking this helper.
    # Force SHA256 because apt-get install --print-uris otherwise selects MD5.
    command = ['apt-get', '-o', 'Acquire::ForceHash=SHA256', '-y', '--no-install-recommends']
    plan = subprocess.check_output(
        [*command, '--print-uris', args.action, *args.packages], text=True)
    print(plan, end='', flush=True)
    packages = package_uris(plan)
    destination = Path('/var/cache/apt/archives') if args.action == 'install' else Path.cwd()
    records = fetch_packages(packages, destination)
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
    if args.action == 'install':
        subprocess.run([*command, '--no-download', 'install', *args.packages], check=True)


if __name__ == '__main__':
    main()
