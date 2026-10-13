"""Export the Yocto reference filesystem for execution on the matching CPU."""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_export import export_examples, write_manifest  # noqa: E402


def export(rootfs, image, generated, configuration, destination):
    rootfs = rootfs.resolve(strict=True)
    generated = generated.resolve(strict=True)
    destination = destination.resolve()
    binaries = export_examples(rootfs, generated, destination, 'Yocto/OpenEmbedded')
    shutil.copy2(image, destination / 'rootfs.tar')
    shutil.copytree(configuration, destination / 'configuration')
    consumer = rootfs / 'usr/libexec/openavc-examples/installed-product'
    installed = destination / 'kit/installed-consumer'
    shutil.copytree(binaries, installed)
    shutil.copy2(consumer, installed / 'example-c')
    # The separately built application uses the same published model and test.
    # Give its actual bytes their own record before executing them unchanged.
    from firmware_export import digest
    for name in ['c-service-build-result.json', 'c-direct-build-result.json']:
        path = installed / name
        record = json.loads(path.read_text())
        record['files']['example-c'] = {'sha256': digest(consumer), 'bytes': consumer.stat().st_size}
        path.write_text(json.dumps(record, indent=2) + '\n')
    write_manifest(destination, binaries)
    print('Yocto example exported to ' + str(destination))
    print('No target binary was executed. Run the checks on the matching native Linux CPU.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['rootfs', 'image', 'generated', 'configuration', 'out']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    export(args.rootfs, args.image, args.generated, args.configuration, args.out)
