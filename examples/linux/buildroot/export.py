"""Export the installed Buildroot examples for execution on the matching CPU."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_export import export_examples, write_manifest  # noqa: E402


def export(output, destination):
    output = output.resolve(strict=True)
    destination = destination.resolve()
    variables = subprocess.check_output(
        ['make', '-s', '-C', str(output), 'printvars', 'VARS=OPENAVC_SDK_BUILDDIR'], text=True)
    build = Path(next(line.split('=', 1)[1] for line in variables.splitlines()
                      if line.startswith('OPENAVC_SDK_BUILDDIR='))).resolve(strict=True)
    if not build.is_relative_to(output):
        raise ValueError('Use the output directory of this Buildroot example build.')
    config = (output / '.config').read_text()
    legal = output / 'legal-info'
    if not (legal / 'manifest.csv').is_file():
        raise ValueError('Run make legal-info in the Buildroot output directory before exporting.')
    if 'BR2_PACKAGE_OPENAVC_SDK_EXAMPLES=y\n' not in config:
        raise ValueError('Enable the invented amplifier examples in the reference configuration.')
    target = output / 'target'
    if 'BR2_PACKAGE_PYTHON_OPENAVC_OEM=y\n' not in config:
        raise ValueError('Enable product management for these complete firmware checks.')
    binaries = export_examples(target, build / 'generated', destination, 'Buildroot',
                               build / 'build-target.json')
    shutil.copy2(output / '.config', destination / 'buildroot.config')
    shutil.copy2(output / 'images/rootfs.tar', destination)
    shutil.copytree(legal, destination / 'legal-info')
    shutil.copytree(Path(__file__).parent / 'patches', destination / 'buildroot-patches')
    packages = json.loads(subprocess.check_output(
        ['make', '-s', '-C', str(output), 'show-info'], text=True))
    (destination / 'buildroot-packages.json').write_text(json.dumps(packages, indent=2) + '\n')
    write_manifest(destination, binaries)
    print('Buildroot example exported to ' + str(destination))
    print('No target binary was executed. Use the matching native Linux CPU to run the checks.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--buildroot-output', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    export(args.buildroot_output, args.out)
