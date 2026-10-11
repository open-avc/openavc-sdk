"""Build and exercise this hardware starter using only bundled sources."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import selectors
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from example_build import prepare
from inspection import inspect_product
from product_checks import Client, read_json, run_checks


def main(args):
    root = Path(__file__).resolve().parent
    project = args.project.resolve() if args.project else root
    supplied = (root / 'bundle.json').exists()
    if supplied and args.language != 'c':
        raise ValueError('This prebuilt library package builds the C adapter. Use the source checkout for the C++ example.')
    if sys.version_info < (3, 12):
        raise ValueError('Use Python 3.12 or newer for this source starter.')
    if sys.platform not in {'linux', 'darwin'}:
        raise ValueError('Use the Linux evaluation environment described in README.md.')
    if args.form == 'service' and sys.platform != 'linux' and not (args.toolchain and args.build_only):
        raise ValueError('The service example requires Linux. Use the direct example on this development host.')
    if not args.build_only and shutil.which('openssl') is None:
        raise ValueError('openssl is missing. Install the prerequisites in docs/building.md.')
    build, built = prepare(args, root, project, supplied)
    target = 'example-' + args.language
    label = args.language + '-' + args.form
    build_record = build / (label + '-build-result.json')
    if args.build_only:
        print('Build complete. Outputs and hashes: ' + str(build_record))
        return
    result = {'status': 'running', 'language': args.language, 'form': args.form, 'checks': {},
              'build_record_sha256': hashlib.sha256(build_record.read_bytes()).hexdigest(),
              'build_host': built['build_host'], 'run_host': {'system': sys.platform, 'machine': os.uname().machine},
              'transferred_build': bool(args.run_built)}
    result_path = build / (label + ('-inspection.json' if args.inspect else '-result.json'))
    product = service = None
    pending = {}
    endpoint_log = str(build / (label + '-endpoint.log'))

    def line(process):
        buffered = pending.get(process, b'')
        deadline = time.monotonic() + 5
        with selectors.DefaultSelector() as select:
            select.register(process.stdout, selectors.EVENT_READ)
            while b'\n' not in buffered:
                if not select.select(max(0, deadline - time.monotonic())):
                    raise ValueError('Example output timed out. Read ' + endpoint_log + '.')
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk or len(buffered) + len(chunk) > 65536:
                    raise ValueError('Example output closed or exceeded the instrument limit. Read ' + endpoint_log + '.')
                buffered += chunk
        first, pending[process] = buffered.split(b'\n', 1)
        return first.decode().strip()

    def hardware(command='snapshot'):
        product.stdin.write(command + '\n')
        product.stdin.flush()
        row = line(product).split()
        if row[0] != 'STATE':
            raise ValueError('Unexpected hardware response: ' + str(row))
        keys = ['writes', 'callbacks', 'wrong_threads', 'channels', 'counter', 'ticks',
                'gain1', 'gain2', 'mute1', 'input', 'reads', 'close_waited', 'pending']
        return {k: float(v) if k.startswith('gain') else int(v) for k, v in zip(keys, row[1:])}

    def check(name, condition):
        result['checks'][name] = bool(condition)
        if not condition:
            raise ValueError(name + ' failed. Read the logs in build/.')

    with tempfile.TemporaryDirectory(prefix='openavc-hardware-example-') as directory:
        private = Path(directory)
        token = secrets.token_hex(32)
        client_id = secrets.token_hex(16)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-keyout', str(private / 'key.pem'), '-out', str(private / 'cert.pem'),
                        '-subj', '/CN=OpenAVC hardware example', '-addext', 'subjectAltName=IP:127.0.0.1'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        (private / 'server.pem').write_bytes((private / 'cert.pem').read_bytes() + (private / 'key.pem').read_bytes())
        tls = ssl.create_default_context(cafile=str(private / 'cert.pem'))
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=tls))
        with Path(endpoint_log).open('w') as log:
            try:
                environment = dict(os.environ)
                for key in ('OPENAVC_TOKEN', 'OPENAVC_PRODUCT_SOCKET'):
                    environment.pop(key, None)
                product_environment = dict(environment)
                if args.form == 'service':
                    product_environment['OPENAVC_PRODUCT_SOCKET'] = str(private / 'product.sock')
                else:
                    product_environment['OPENAVC_TOKEN'] = token
                product = subprocess.Popen([str(build / target), str(build / 'generated/model.json'),
                    str(private / 'server.pem') if args.form == 'direct' else '-'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
                    env=product_environment)
                ready = line(product).split()
                if len(ready) != 2 or ready[0] != 'READY':
                    raise ValueError('The example did not start. Read ' + endpoint_log + '.')
                if args.form == 'service':
                    if ready[1] != '0':
                        raise ValueError('The service product unexpectedly opened a network listener.')
                    service = subprocess.Popen([str(build / 'openavc-device-service'), str(build / 'generated/model.json'),
                        str(private / 'server.pem'), str(private / 'product.sock')],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
                        env={**environment, 'OPENAVC_TOKEN': token})
                    ready = line(service).split()
                    if len(ready) != 2 or ready[0] != 'READY':
                        raise ValueError('The service did not start. Read ' + endpoint_log + '.')
                base = 'https://127.0.0.1:' + ready[1]

                def request(path, payload=None):
                    req = urllib.request.Request(base + path,
                        data=json.dumps(payload).encode() if payload is not None else None,
                        headers={'Authorization': 'Bearer ' + token, 'X-OpenAVC-Client': client_id, 'Content-Type': 'application/json'})
                    try:
                        response = client.open(req, timeout=4)
                    except urllib.error.HTTPError as exc:
                        response = exc
                    with response:
                        return response.status, json.load(response)

                def until(probe):
                    deadline = time.monotonic() + 4
                    while True:
                        value = probe()
                        if value:
                            return value
                        if time.monotonic() > deadline:
                            raise ValueError('Feedback timed out. Read ' + endpoint_log + '; do not replay an uncertain command.')
                        time.sleep(.01)

                if args.checks:
                    until(lambda: request('/v1/snapshot')[0] == 200)
                    report = run_checks(Client(base, {'token': token}, private / 'cert.pem'),
                                        read_json(build / 'generated/model.json'), args.out, read_json(args.checks))
                    result.update(status=report['status'], product_checks=str(args.out / 'report.json'))
                elif args.inspect:
                    result['inspection'] = inspect_product(request, json.loads((build / 'generated/model.json').read_text()))
                    result['status'] = 'inspected'
                else:
                    until(lambda: request('/v1/snapshot')[0] == 200)
                    status, description = request('/v1/description')
                    check('description_available', status == 200)
                    check('device_identity', description['identity'] == os.environ.get('OPENAVC_DEVICE_ID', 'acme-native'))
                    result['device_identity'] = description['identity']
                    session = description['session']
                    status, reply = request('/v1/command', {'session': session, 'id': 1, 'command': 'increment', 'params': {}})
                    check('immediate_command_completed', status == 200 and reply['outcome'] == 'completed')
                    actual = hardware()
                    until(lambda: request('/v1/snapshot')[1]['state']['counter'] == actual['counter'])
                    check('independent_hardware_readback', actual['counter'] == 1 and actual['writes'] == 1 and actual['wrong_threads'] == 0)
                    hardware('delay 900')
                    status, reply = request('/v1/command', {'session': session, 'id': 2, 'command': 'set_gain', 'params': {'channel': 1, 'gain': -31}})
                    check('delayed_command_pending', status == 202 and reply['outcome'] == 'uncertain')
                    active = hardware('external -7.5')
                    until(lambda: request('/v1/snapshot')[1]['children'][1]['state']['gain'] == -7.5)
                    check('external_feedback_during_command', active['pending'] != 0 and hardware()['pending'] != 0)
                    until(lambda: request('/v1/commands/2')[1]['outcome'] == 'completed')
                    actual = hardware()
                    until(lambda: request('/v1/snapshot')[1]['children'][0]['state']['gain'] == actual['gain1'])
                    check('delayed_hardware_completed_once', actual['gain1'] == -31 and actual['writes'] == 2 and actual['callbacks'] == 2 and actual['wrong_threads'] == 0)
                    result.update(status='passed', hardware=actual)
                result.update(model_sha256=hashlib.sha256((build / 'generated/model.json').read_bytes()).hexdigest(),
                              binary_sha256=hashlib.sha256((build / target).read_bytes()).hexdigest())
                if service:
                    result['service_sha256'] = hashlib.sha256((build / 'openavc-device-service').read_bytes()).hexdigest()
            except BaseException as exc:
                result.update(status='failed', error=str(exc))
                raise
            finally:
                for name, process, stop in [('service', service, 'quit'), ('product', product, 'stop')]:
                    if process and process.poll() is None:
                        try:
                            process.stdin.write(stop + '\n')
                            process.stdin.flush()
                        except BrokenPipeError:
                            pass
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                    result[name + '_exit'] = process.returncode if process else None
                    if process:
                        remaining = pending.get(process, b'')
                        os.set_blocking(process.stdout.fileno(), False)
                        complete = False
                        while len(remaining) <= 65536:
                            try:
                                chunk = os.read(process.stdout.fileno(), 4096)
                            except BlockingIOError:
                                break
                            if not chunk:
                                complete = True
                                break
                            remaining += chunk
                        (build / (label + '-' + name + '-output.log')).write_bytes(remaining)
                        result[name + '_output_complete'] = complete
                        if not complete:
                            result['status'] = 'failed'
                        process.stdout.close()
                        process.stdin.close()
                    if (process and process.returncode != 0) or (name == 'product' and not process):
                        result['status'] = 'failed'
                if args.form == 'service' and (not service or (private / 'product.sock').exists()):
                    result['status'] = 'failed'
                result_path.write_text(json.dumps(result, indent=2) + '\n')
    if result['status'] not in {'passed', 'inspected'}:
        raise ValueError('Example cleanup failed. Read ' + endpoint_log + '.')
    if args.checks:
        print('Product checks passed. Report: ' + str(args.out / 'report.json'))
        print('Coverage: protocol and reported state. Verify physical effects independently on your product.')
        return
    if args.inspect:
        print('Inspection saved: ' + str(result_path))
        print('Feedback: ' + result['inspection']['feedback'] + '. No hardware commands were sent.')
        if result['inspection']['feedback'] != 'current':
            print('Check hardware readback and ' + endpoint_log + '.')
        return
    print(args.language.upper() + ' ' + args.form + ' example passed: commands, independent feedback and delayed completion.')
    print('Use --build-only or --inspect after editing the product model or hardware mapping. See README.md.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=['c', 'cpp'], default='c')
    parser.add_argument('--form', choices=['direct', 'service'], default='service' if sys.platform == 'linux' else 'direct')
    parser.add_argument('--openssl', type=Path)
    parser.add_argument('--build-dir', type=Path, help='Build directory; defaults to build/ in the product directory.')
    build_mode = parser.add_mutually_exclusive_group()
    build_mode.add_argument('--toolchain', type=Path, help='CMake firmware toolchain; requires --build-only.')
    build_mode.add_argument('--run-built', type=Path, help='Verify and run an existing build directory without compiling.')
    parser.add_argument('--project', type=Path, help='Your product directory containing product.json and hardware.c or hardware.cpp.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--build-only', action='store_true', help='Validate the product and compile without starting hardware.')
    mode.add_argument('--inspect', action='store_true', help='Start the selected integration, read capabilities/state and stop without sending hardware commands.')
    mode.add_argument('--exercise-demo', action='store_true', help='Send the amplifier demonstration commands and verify its simulated hardware.')
    mode.add_argument('--checks', type=Path, help='Run your selected command/readback checks after building and starting the adapter.')
    parser.add_argument('--out', type=Path, help='New evidence directory, required with --checks.')
    options = parser.parse_args()
    if options.toolchain and not options.build_only:
        parser.error('Use --toolchain with --build-only, then --run-built on the target.')
    if options.run_built and (options.build_only or options.build_dir or options.project or options.openssl):
        parser.error('--run-built uses the transferred build; omit build options.')
    if bool(options.checks) != bool(options.out):
        parser.error('Use --checks and --out together.')
    if not options.build_only and not options.exercise_demo and not options.checks:
        options.inspect = True
    try:
        main(options)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + '\n')
