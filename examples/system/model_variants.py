"""Compile two revisions of the invented amplifier for compatibility checks."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def build(destination):
    root = Path(__file__).resolve().parents[2]
    base = json.loads((root / 'product.json').read_text())
    source = (root / 'sample/example.c').read_text() + (
        '\noavc_outcome oavc_hw_advance(void *c, oavc_token token) { '
        'return begin((product *)c, token, 4, 0, 0); }\n')
    for label, version in [('additive', '0.0.2'), ('removed', '0.0.3')]:
        folder = destination.resolve() / label
        folder.mkdir(parents=True)
        model = json.loads(json.dumps(base))
        model['version'] = version
        model['commands']['advance'] = {'label': 'Advance Once', 'params': {}}
        if label == 'removed':
            del model['commands']['set_gain']
        (folder / 'product.json').write_text(json.dumps(model, indent=2) + '\n')
        (folder / 'example.c').write_text(source)
        (folder / 'CMakeLists.txt').write_text(f'''cmake_minimum_required(VERSION 3.16)
project(evolution_example LANGUAGES C CXX)
set(OPENAVC_PRODUCT "{folder}/product.json" CACHE FILEPATH "")
set(OPENAVC_BUILD_EXAMPLES OFF CACHE BOOL "")
add_subdirectory("{root}" sdk)
add_executable(example-evolution "{folder}/example.c")
target_compile_options(example-evolution PRIVATE -Wall -Wextra -Werror -Wno-missing-field-initializers)
target_link_libraries(example-evolution PRIVATE adapter)
''')
        subprocess.run(['cmake', '-S', str(folder), '-B', str(folder / 'build'),
                        '-DCMAKE_BUILD_TYPE=Release'], check=True, timeout=120)
        subprocess.run(['cmake', '--build', str(folder / 'build'), '--target',
                        'example-evolution', 'openavc-device-service', '--parallel', '2'],
                       check=True, timeout=120)
        names = ['product.json', 'example.c', 'build/example-evolution',
                 'build/sdk/openavc-device-service', 'build/sdk/generated/model.json']
        files = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in names}
        (folder / 'manifest.json').write_text(json.dumps({
            'variant': label, 'version': version, 'files': files}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    build(parser.parse_args().out)
