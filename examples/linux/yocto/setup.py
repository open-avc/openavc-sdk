"""Prepare a separate, pinned Yocto reference build for the OpenAVC examples."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
SOURCES = {
    'openembedded-core': ('https://git.openembedded.org/openembedded-core',
                          'ef022bf82d79015802309d14c28b13373ebe53f5'),
    'bitbake': ('https://git.openembedded.org/bitbake',
                'fae9db3168dbff1b8c76fe9c6726a9687ff97514'),
    'meta-openembedded': ('https://git.openembedded.org/meta-openembedded',
                          'f28a144aa0a0ae05ca2ee91e1aef6aa05f6a12db'),
}


def fetch_revision(path, revision):
    """Retry interrupted downloads, never revision, credential or TLS refusals."""
    command = ['git', '-C', str(path), 'fetch', '--depth', '1', 'origin', revision]
    for attempt in range(1, 4):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env={**os.environ, 'LC_ALL': 'C'})
        print(result.stdout, end='', flush=True)
        if result.returncode == 0:
            return
        interrupted = re.search(r'curl (?:5|6|7|18|28|52|55|56|92)\b|'
                                r'Connection reset by peer|Could not resolve host|'
                                r'Failed to connect|The requested URL returned error: 5\d\d',
                                result.stdout)
        if not interrupted or attempt == 3:
            result.check_returncode()
        print(f'Source download interrupted; retrying the same revision ({attempt + 1}/3).', flush=True)
        time.sleep(attempt * 2)


def setup(args):
    output = args.out.resolve()
    if any(char in str(output) + str(ROOT) for char in ' \t\n"'):
        raise ValueError('Use checkout and build paths without spaces or quotation marks for BitBake.')
    if not re.fullmatch('[0-9a-f]{40}', args.sdk_revision):
        raise ValueError('Select the complete, published SDK Git revision.')
    if args.tasks < 1:
        raise ValueError('Select at least one concurrent build task.')
    output.mkdir(parents=True, exist_ok=False)
    layers = output / 'layers'
    layers.mkdir()
    for name, (url, revision) in SOURCES.items():
        path = layers / name
        subprocess.run(['git', 'init', str(path)], check=True)
        subprocess.run(['git', '-C', str(path), 'remote', 'add', 'origin', url], check=True)
        fetch_revision(path, revision)
        subprocess.run(['git', '-C', str(path), 'checkout', '--detach', 'FETCH_HEAD'], check=True)
        actual = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != revision:
            raise ValueError('The fetched source does not match the selected revision: ' + name)
    configuration = output / 'build/conf'
    configuration.mkdir(parents=True)
    selected = [layers / 'openembedded-core/meta', layers / 'meta-openembedded/meta-oe',
                layers / 'meta-openembedded/meta-python', Path(__file__).parent / 'meta-openavc']
    (configuration / 'bblayers.conf').write_text(
        'LCONF_VERSION = "7"\nBBPATH = "${TOPDIR}"\nBBFILES ?= ""\nBBLAYERS = "' +
        ' '.join(str(path) for path in selected) + '"\n')
    (configuration / 'local.conf').write_text(
        f'MACHINE = "{args.machine}"\n'
        f'OPENAVC_SDK_REVISION = "{args.sdk_revision}"\n'
        'PACKAGECONFIG:append:pn-openavc-sdk = " examples"\n'
        'INIT_MANAGER = "mdev-busybox"\n'
        f'BB_NUMBER_THREADS = "{args.tasks}"\nPARALLEL_MAKE = "-j2"\n'
        'INHERIT += "rm_work"\n'
        'RM_WORK_EXCLUDE += "openavc-sdk openavc-example-image"\n'
        'BB_DISKMON_DIRS = "STOPTASKS,${TMPDIR},4G,100K HALT,${TMPDIR},2G,50K"\n'
        'BB_HASHSERVE = "auto"\nBB_SIGNATURE_HANDLER = "OEEquivHash"\n'
        'require conf/fragments/yocto/sstate-mirror-cdn.conf\n')
    (configuration / 'sources.json').write_text(json.dumps(
        {'sources': SOURCES, 'sdk_revision': args.sdk_revision, 'machine': args.machine,
         'concurrent_tasks': args.tasks}, indent=2) + '\n')
    print('Prepared ' + str(output / 'build'))
    print('Source layers/openembedded-core/oe-init-build-env with the absolute build and BitBake paths.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--machine', choices=['qemuarm64', 'qemux86-64'], required=True)
    parser.add_argument('--sdk-revision', required=True)
    parser.add_argument('--tasks', type=int, default=2, help='Concurrent BitBake tasks (default: 2)')
    setup(parser.parse_args())
