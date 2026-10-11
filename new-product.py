"""Create a separate product project from the working hardware example."""
import argparse
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent


def create(out, language):
    source = ROOT / ('sample/example.' + language)
    if not source.is_file():
        raise ValueError('Use the SDK source checkout for the C++ starter.')
    revision = None
    if (ROOT / '.git').exists():
        repository = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--show-toplevel'],
                                    capture_output=True, text=True)
        if repository.returncode == 0 and Path(repository.stdout.strip()).resolve() == ROOT:
            revision = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(ROOT / 'product.json', out / 'product.json')
    shutil.copy2(source, out / ('hardware.' + language))
    shutil.copy2(ROOT / 'sample/checks.json', out / 'checks.json')
    (out / '.gitignore').write_text('/build/\n/sdk/\n__pycache__/\n')
    (out / 'README.md').write_text('''# Product hardware integration

This starter runs an invented amplifier. Replace `product.json` with your
capabilities and implement the callbacks in `hardware.LANGUAGE` using your
hardware APIs. Edit `checks.json` to select safe operations and expected readback.

With the OpenAVC SDK in `sdk/`, run these commands from this directory:

```sh
python3 sdk/try-example.py --project . --language LANGUAGE --build-only
python3 sdk/try-example.py --project . --language LANGUAGE --inspect
python3 sdk/try-example.py --project . --language LANGUAGE --checks checks.json --out build/check-1
```

The first command compiles without starting hardware. Inspection reads state
without sending hardware commands. The last command executes your checks and
deliberately repeats each completed request ID to check duplicate handling.
Use a new evidence directory each time. An interrupted operation requires
inspection before another run.

See `sdk/docs/product-testing.md` for check-file fields, resource measurement
and diagnostics. `sdk/docs/hardware-integration.md` describes the callbacks.
The starter has its own product event loop; keep hardware work on the thread
your product permits. It does not install services or configure your firmware.
'''.replace('LANGUAGE', language))
    if revision:
        workflows = out / '.github/workflows'
        workflows.mkdir(parents=True)
        (workflows / 'check.yml').write_text('''name: Product integration
on: [push, pull_request, workflow_dispatch]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/checkout@v4
        with:
          repository: open-avc/openavc-sdk
          ref: SDK_REVISION
          path: sdk
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: sudo apt-get update && sudo apt-get install -y cmake build-essential libssl-dev openssl
      - run: python sdk/try-example.py --project . --language LANGUAGE --build-only
'''.replace('SDK_REVISION', revision).replace('LANGUAGE', language))
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='New product directory')
    parser.add_argument('--language', choices=['c', 'cpp'], default='c')
    args = parser.parse_args()
    try:
        create(args.out, args.language)
    except (ValueError, OSError, subprocess.SubprocessError):
        parser.exit(1, 'Product could not be created. Use a new directory and a complete SDK checkout or library archive.\n')
    print('Product created: ' + str(args.out.resolve()))
    print('Build: python3 ' + str(ROOT / 'try-example.py') + ' --project ' + str(args.out) + ' --language ' + args.language + ' --build-only')
