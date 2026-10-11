"""Compile the hardware example or verify a transferred build before running it."""
import hashlib
import json
import platform
import shutil
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(args, root, project, supplied):
    build = (args.run_built or args.build_dir or project / 'build').resolve()
    target = 'example-' + args.language
    label = args.language + '-' + args.form
    record = build / (label + '-build-result.json')
    files = [target, 'generated/model.json', 'generated/commands.h', 'generated/commands.c',
             'generated/model.provenance.json', 'generated/provenance.json', 'build-target.json']
    if args.form == 'service':
        files.append('openavc-device-service')
    if args.run_built:
        built = json.loads(record.read_text())
        if (built.get('format') != 1 or built.get('status') != 'built'
                or built.get('language') != args.language or built.get('form') != args.form):
            raise ValueError('Use a successful build record from this SDK for the selected language and form.')
        for name in files:
            expected = built['files'][name]
            if digest(build / name) != expected['sha256'] or (build / name).stat().st_size != expected['bytes']:
                raise ValueError('Transferred build changed: ' + name + '. Copy the complete original build again.')
        target_host = json.loads((build / 'build-target.json').read_text())
        machines = {'arm64': 'aarch64', 'amd64': 'x86_64'}
        def machine(value):
            return machines.get(value.lower(), value.lower())
        if (target_host['system'] != platform.system()
                or machine(target_host['machine']) != machine(platform.machine())):
            raise ValueError('Run these binaries on their target: ' + str(target_host) + '.')
        if built['contract_sha256'] != digest(root / 'sdk/contract/origin.json'):
            raise ValueError('Use the SDK checkout that supplied this build contract.')
        return build, built

    if supplied and args.toolchain:
        raise ValueError('Use the source checkout when building with a firmware toolchain.')
    for tool in ('cmake',) + (() if args.toolchain else ('cc',) + (() if supplied else ('c++',))):
        if shutil.which(tool) is None:
            raise ValueError(f'{tool} is missing. Install the prerequisites in docs/building.md.')
    build.mkdir(parents=True, exist_ok=True)
    commands = [['cmake', '-S', str(root), '-B', str(build), '-DCMAKE_BUILD_TYPE=Release',
                 '-DPython3_EXECUTABLE=' + sys.executable,
                 '-DOPENAVC_PRODUCT=' + str(project / 'product.json'),
                 '-DOPENAVC_EXAMPLE_' + args.language + '=' + str(
                     project / ('hardware.' + args.language) if args.project else root / ('sample/example.' + args.language)),
                 *(['-DCMAKE_TOOLCHAIN_FILE=' + str(args.toolchain.resolve())] if args.toolchain else []),
                 *(['-DOPENSSL_ROOT_DIR=' + str(args.openssl.resolve())] if args.openssl else [])],
                ['cmake', '--build', str(build), '--target', target,
                 *(['openavc-device-service'] if args.form == 'service' else []), '--parallel', '1']]
    built = {'format': 1, 'status': 'building', 'language': args.language, 'form': args.form,
             'validation': 'model_and_compilation', 'commands': commands,
             'build_host': {'system': platform.system(), 'machine': platform.machine()},
             'contract_sha256': digest(root / 'sdk/contract/origin.json')}
    record.write_text(json.dumps(built, indent=2) + '\n')
    try:
        with (build / (label + '-build.log')).open('w') as log:
            for command in commands:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=300, check=True)
        built.update(status='built', files={name: {'sha256': digest(build / name),
                                                 'bytes': (build / name).stat().st_size} for name in files})
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        built['status'] = 'failed'
        raise ValueError('Build failed. Read ' + str(build / (label + '-build.log')) + '.') from error
    finally:
        record.write_text(json.dumps(built, indent=2) + '\n')
    return build, built
