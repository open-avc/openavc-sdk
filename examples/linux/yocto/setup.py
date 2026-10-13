"""Prepare a separate, pinned Yocto reference build for the OpenAVC examples."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
SOURCES = {
    'openembedded-core': ('https://git.openembedded.org/openembedded-core',
                          'ef022bf82d79015802309d14c28b13373ebe53f5'),
    'bitbake': ('https://git.openembedded.org/bitbake',
                'fae9db3168dbff1b8c76fe9c6726a9687ff97514'),
    'meta-openembedded': ('https://git.openembedded.org/meta-openembedded',
                          'f28a144aa0a0ae05ca2ee91e1aef6aa05f6a12db'),
}


def setup(args):
    output = args.out.resolve()
    if any(char in str(output) + str(ROOT) for char in ' \t\n"'):
        raise ValueError('Use checkout and build paths without spaces or quotation marks for BitBake.')
    if not re.fullmatch('[0-9a-f]{40}', args.sdk_revision):
        raise ValueError('Select the complete, published SDK Git revision.')
    output.mkdir(parents=True, exist_ok=False)
    layers = output / 'layers'
    layers.mkdir()
    for name, (url, revision) in SOURCES.items():
        path = layers / name
        subprocess.run(['git', 'init', str(path)], check=True)
        subprocess.run(['git', '-C', str(path), 'remote', 'add', 'origin', url], check=True)
        subprocess.run(['git', '-C', str(path), 'fetch', '--depth', '1', 'origin', revision], check=True)
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
        'BB_NUMBER_THREADS = "2"\nPARALLEL_MAKE = "-j2"\n'
        'INHERIT += "rm_work"\n'
        'RM_WORK_EXCLUDE += "openavc-sdk openavc-example-image"\n'
        'BB_DISKMON_DIRS = "STOPTASKS,${TMPDIR},4G,100K HALT,${TMPDIR},2G,50K"\n'
        'BB_HASHSERVE = "auto"\nBB_SIGNATURE_HANDLER = "OEEquivHash"\n'
        'require conf/fragments/yocto/sstate-mirror-cdn.conf\n')
    (configuration / 'sources.json').write_text(json.dumps(
        {'sources': SOURCES, 'sdk_revision': args.sdk_revision, 'machine': args.machine}, indent=2) + '\n')
    print('Prepared ' + str(output / 'build'))
    print('Source layers/openembedded-core/oe-init-build-env with the absolute build and BitBake paths.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--machine', choices=['qemuarm64', 'qemux86-64'], required=True)
    parser.add_argument('--sdk-revision', required=True)
    setup(parser.parse_args())
