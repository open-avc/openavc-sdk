"""Run example products and ordinary equipment on an isolated Docker network."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent


def run(args):
    if shutil.which('docker') is None:
        raise ValueError('Install and start Docker, then run this command again. See docs/system-example.md.')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    out.chmod(0o700)
    docker = ['docker', *(['--context', args.docker_context] if args.docker_context else [])]
    prefix = 'openavc-example-' + secrets.token_hex(5)
    image = 'openavc-sdk-example:local'
    containers = []
    network_created = False
    layout = {'arrangement': args.arrangement, 'product_count': args.products,
              'recovery': args.recovery, 'docker': docker, 'network': prefix, 'products': {},
              'contract': json.loads((ROOT / 'sdk/contract/origin.json').read_text()),
              'containers': containers, 'status': 'starting'}

    def save():
        (out / 'run.json').write_text(json.dumps(layout, indent=2) + '\n')

    def call(command, *, capture=True, input=None):
        result = subprocess.run(docker + command, input=input, text=True,
            stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None,
            timeout=1800)
        if result.returncode:
            raise RuntimeError('Docker command failed: ' + ' '.join(command[:3]) + '\n' + (result.stderr or '')[-4000:])
        return result.stdout.strip() if capture else ''

    def copy(source, container, destination):
        # macOS Docker copies can preserve the workstation's owner/ACL. Give
        # disposable container files their intended owner without granting
        # the running example extra filesystem capabilities.
        def owned(member):
            member.uid = member.gid = 0
            member.uname = member.gname = 'root'
            member.pax_headers = {}
            return member
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as output:
            output.add(source, arcname=destination.lstrip('/'), filter=owned)
        result = subprocess.run(docker + ['cp', '-', container + ':/'], input=archive.getvalue(), capture_output=True)
        if result.returncode:
            raise RuntimeError('Could not provision the example files: ' + result.stderr.decode())

    def create(name, command, *, environment=None, ports=None):
        container = prefix + '-' + name
        parameters = ['create', '--name', container, '--hostname', name, '--network', prefix,
            '--label', 'openavc.sdk.example=' + prefix, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges']
        for key, value in (environment or {}).items():
            parameters += ['--env', key + '=' + value]
        for host, internal in (ports or {}).items():
            parameters += ['--publish', f'127.0.0.1:{host}:{internal}']
        parameters += [image, *command]
        call(parameters)
        containers.append(container)
        save()
        return container

    def management(container, field=None, enabled=None):
        code = (
            "import json,pathlib,urllib.request; "
            "token=pathlib.Path('/data/management.token').read_text(); "
            "headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}; "
            "url='http://127.0.0.1:9081/oem/settings'; "
            "state=json.load(urllib.request.urlopen(urllib.request.Request(url,headers=headers))); "
        )
        if field:
            code += (
                f"body=json.dumps({{'revision':state['revision'],'field':{field!r},'enabled':{enabled!r}}}).encode(); "
                "state=json.load(urllib.request.urlopen(urllib.request.Request(url,data=body,headers=headers),timeout=60)); "
            )
        return json.loads(call(['exec', container, 'python', '-c', code + 'print(json.dumps(state))']))

    def ready(container):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            running = call(['inspect', '-f', '{{.State.Running}}', container]) == 'true'
            logs = call(['logs', container])
            if not running:
                raise RuntimeError('An example process exited. Read its saved container log.')
            if 'READY' in logs.splitlines():
                return
            time.sleep(.2)
        raise TimeoutError('Example startup did not finish. Read its saved container log.')

    credentials = {'username': 'engineer', 'password': secrets.token_urlsafe(18)}
    (out / 'product-login.json').write_text(json.dumps(credentials, indent=2) + '\n')
    (out / 'product-login.json').chmod(0o600)
    save()
    try:
        call(['info', '--format', '{{.OSType}}'])
        print('Building the Linux example and the pinned OpenAVC server. The first build downloads dependencies.', flush=True)
        with (out / 'build.log').open('w') as log:
            built = subprocess.run(docker + ['build', '-t', image, '-f', str(ROOT / 'examples/system/Dockerfile'), str(ROOT)],
                                   stdout=log, stderr=subprocess.STDOUT)
        if built.returncode:
            raise RuntimeError('The example image did not build. Read ' + str(out / 'build.log'))
        # Use the built content, even if another example later updates the tag.
        image = call(['image', 'inspect', '--format', '{{.Id}}', image])
        layout['image'] = image
        call(['network', 'create', '--internal', '--label', 'openavc.sdk.example=' + prefix, prefix])
        network_created = True
        ordinary = create('ordinary', ['examples/system/ordinary.py'])
        call(['start', ordinary])
        ready(ordinary)
        addresses = {}
        certificates = []
        for number in range(1, args.products + 1):
            name = f'product{number}'
            port = args.port + (number - 1) * 2
            server_included = args.products == 5 or number == 1
            container = create(name, ['examples/system/product.py'], environment={
                'EXAMPLE_IDENTITY': prefix + '-' + name, 'EXAMPLE_INCLUDE_SERVER': '1' if server_included else '0',
                'EXAMPLE_SERVER_PORT': str(port)})
            # docker cp can create /data before the product process starts.
            seed = out / name
            seed.mkdir()
            shutil.copy2(out / 'product-login.json', seed / 'login.json')
            copy(seed, container, '/data')
            call(['start', container])
            ready(container)
            address = call(['inspect', '-f', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}', container])
            addresses[name] = address
            certificate = seed / 'ca.pem'
            call(['cp', container + ':/data/ca.pem', str(certificate)])
            certificates.append(certificate.read_bytes())
            layout['products'][name] = {'container': container, 'address': address,
                'management_url': f'http://127.0.0.1:{port + 1}/oem/', 'server_url': f'http://127.0.0.1:{port}',
                'server_included': server_included}
        addresses['ordinary'] = call(['inspect', '-f', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}', ordinary])
        trust = out / 'trust.pem'
        trust.write_bytes(b'\n'.join(certificates))
        module = importlib.util.spec_from_file_location('example_project', ROOT / 'examples/system/project.py')
        project_module = importlib.util.module_from_spec(module)
        module.loader.exec_module(project_module)
        embedded = args.arrangement == 'embedded'
        project = out / 'project.avc'
        project.write_text(json.dumps(project_module.project(addresses, credentials, embedded=embedded), indent=2) + '\n')
        for product in layout['products'].values():
            copy(trust, product['container'], '/data/trust.pem')
        first = layout['products']['product1']
        if embedded:
            call(['exec', first['container'], 'mkdir', '-p', '/data/openavc/server'])
            copy(project, first['container'], '/data/openavc/server/project.avc')
            management(first['container'], 'server', True)
            controller, server_url = first['container'], first['server_url']
        else:
            seed = out / 'controller'
            seed.mkdir()
            shutil.copy2(trust, seed / 'trust.pem')
            shutil.copy2(project, seed / 'project.avc')
            server_url = f'http://127.0.0.1:{args.port + args.products * 2}'
            controller = create('controller', ['-m', 'openavc.main'], environment={
                'OPENAVC_DATA_DIR': '/data', 'OPENAVC_PROJECT': '/data/project.avc', 'OPENAVC_BIND': '0.0.0.0',
                'OPENAVC_ALLOW_ANONYMOUS': 'false', 'OPENAVC_CLOUD_ENABLED': 'false',
                'OPENAVC_PORT80_REDIRECT': 'false', 'SSL_CERT_FILE': '/data/trust.pem'})
            copy(seed, controller, '/data')
            call(['start', controller])
        # Docker does not publish ports from an internal network. Only this
        # HTTP gateway joins the normal bridge; all equipment stays isolated.
        # Preserve Host for the example management page's same-origin check.
        routes = {}
        for index, product in enumerate(layout['products'].values()):
            routes[8080 + index * 2] = product['container'] + ':8080'
            routes[8081 + index * 2] = product['container'] + ':9081'
        routes[8080 + args.products * 2] = controller + ':8080'
        configuration = ['events {}', 'http {',
            'map $http_upgrade $connection_upgrade { default upgrade; "" close; }',
            'resolver 127.0.0.11 valid=10s;']
        for port, target in routes.items():
            configuration.append('server { listen ' + str(port) + '; location / { '
                'set $target "' + target + '"; proxy_pass http://$target; proxy_http_version 1.1; '
                'proxy_set_header Host $http_host; proxy_set_header Upgrade $http_upgrade; '
                'proxy_set_header Connection $connection_upgrade; proxy_read_timeout 300s; } }')
        configuration.append('}')
        proxy_config = out / 'gateway.conf'
        proxy_config.write_text('\n'.join(configuration) + '\n')
        gateway = prefix + '-gateway'
        parameters = ['create', '--name', gateway, '--network', 'bridge', '--label', 'openavc.sdk.example=' + prefix]
        for offset in range(args.products * 2 + 1):
            parameters += ['--publish', f'127.0.0.1:{args.port + offset}:{8080 + offset}']
        parameters += ['nginx:stable-alpine']
        call(parameters)
        containers.append(gateway)
        save()
        call(['network', 'connect', prefix, gateway])
        copy(proxy_config, gateway, '/etc/nginx/nginx.conf')
        call(['start', gateway])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 45
        while True:
            try:
                with opener.open(server_url + '/api/health', timeout=2) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError('OpenAVC did not become ready. Read its container log.')
                time.sleep(.2)
        layout.update(status='ready', server_url=server_url, controller=controller, ordinary=ordinary, addresses=addresses)
        save()
        print(f'\nOpenAVC Programmer: {server_url}/programmer\nOpenAVC panel: {server_url}/panel')
        print('Create your OpenAVC administrator account on the normal first-run page.')
        for name, product in layout['products'].items():
            print(name + ' settings: ' + product['management_url'])
        print(f"Product login: {credentials['username']}\nProduct password: {credentials['password']}")
        print(f'The example project already includes {args.products} amplifiers and the ordinary TCP device.')
        print('Press Ctrl+C to stop this example. Diagnostics will be saved in ' + str(out), flush=True)
        if args.check:
            subprocess.run([sys.executable, str(ROOT / 'examples/system/check.py'), '--run', str(out)], check=True)
            layout['status'] = 'passed'
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        layout['status'] = 'stopped'
    except BaseException:
        layout['status'] = 'failed'
        raise
    finally:
        for container in reversed(containers):
            with contextlib.suppress(Exception):
                result = subprocess.run(docker + ['logs', container], capture_output=True, text=True)
                (out / (container + '.log')).write_text(result.stdout + result.stderr)
                call(['stop', '--time', '20', container])
                call(['cp', container + ':/data', str(out / (container + '-data'))]) if '-product' in container else None
            with contextlib.suppress(Exception):
                call(['rm', container])
        if network_created:
            with contextlib.suppress(Exception):
                call(['network', 'rm', prefix])
        save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arrangement', choices=['external', 'embedded'], default='external')
    parser.add_argument('--products', type=int, choices=[2, 5], default=2, help='Number of example SDK products. All five include a stopped server; embedded starts only product 1.')
    parser.add_argument('--out', type=Path, default=Path('build/system-example'))
    parser.add_argument('--port', type=int, default=18080, help='First localhost port; uses two per product plus one for the controller.')
    parser.add_argument('--docker-context', help='Use a named Docker context without changing your active context.')
    parser.add_argument('--check', action='store_true', help='Exercise the sample equipment and stop after verification.')
    parser.add_argument('--recovery', action='store_true', help='With --check, stop and restart one invented peer and verify recovery.')
    options = parser.parse_args()
    if not 1024 <= options.port <= 65535 - options.products * 2:
        parser.error('--port must leave room for two ports per product and one controller port below 65536.')
    if options.recovery and not options.check:
        parser.error('--recovery requires --check.')
    try:
        run(options)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
