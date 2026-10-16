"""Observe two ordinary controllers using explicit equipment credentials."""
from concurrent.futures import ThreadPoolExecutor
import json
import secrets
import subprocess


REQUEST = """
import base64,json,pathlib,sys,urllib.error,urllib.request
args=json.loads(sys.argv[1])
account=json.loads(pathlib.Path('/data/admin.json').read_text())
headers={'Authorization':'Basic '+base64.b64encode((account['username']+':'+account['password']).encode()).decode(),
         'Content-Type':'application/json',**args.get('headers',{})}
body=account if args['path']=='/api/auth/setup' else args.get('body')
request=urllib.request.Request('http://127.0.0.1:8080'+args['path'],method=args['method'],headers=headers,
                               data=json.dumps(body).encode() if body is not None else None)
try:
    response=urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=15)
except urllib.error.HTTPError as error:
    response=error
with response:
    print(json.dumps({'status':response.status,'body':json.load(response),'etag':response.headers.get('ETag')}))
"""


def check_authority(layout, report, *, api, docker, physical, until, recorded, credentials):
    secondary = layout['secondary_controller']
    assert set(json.loads(docker('inspect', '-f', '{{json .NetworkSettings.Networks}}', secondary))) == {layout['network']}

    def other(path, body=None, method='GET', expected=(200,), headers=None):
        result = json.loads(docker('exec', secondary, 'python', '-c', REQUEST, json.dumps({
            'path': path, 'body': body, 'method': method, 'headers': headers or {}})))
        assert result['status'] in expected, (path, result)
        return result

    def ready():
        try:
            return other('/api/health', expected=(200, 503))['status'] == 200
        except subprocess.CalledProcessError:
            return False

    observation = {'status': 'running', 'secondary_controller': secondary}
    report['observations']['controller_authority'] = observation
    command = {'command': 'increment', 'params': {}}
    local = 'local' if layout['arrangement'] == 'embedded' else 'product1'
    try:
        until(ready, timeout=45)
        other('/api/auth/setup', method='POST')
        until(lambda: other('/api/devices/own')['body']['connected'])
        first, second = physical('product1'), physical('product2')
        with ThreadPoolExecutor(max_workers=2) as executor:
            left = executor.submit(api, '/api/devices/' + local + '/command', command)
            right = executor.submit(other, '/api/devices/own/command', command, 'POST')
            left.result(); right.result()
        assert physical('product1')['counter'] == first['counter'] + 1
        assert physical('product2')['counter'] == second['counter'] + 1
        observation['independent_systems'] = {'first': physical('product1'), 'second': physical('product2')}
        recorded('Independent controllers operate their selected products on the same LAN')
        before = physical('product2')
        with ThreadPoolExecutor(max_workers=2) as executor:
            left = executor.submit(api, '/api/devices/product2/command', command)
            right = executor.submit(other, '/api/devices/own/command', command, 'POST')
            left.result(); right.result()
        shared = physical('product2')
        for field in ('counter', 'callbacks', 'writes'):
            assert shared[field] == before[field] + 2
        assert shared['wrong_threads'] == 0
        observation['shared_equipment'] = {'before': before, 'after': shared}
        recorded('Both explicitly authorized controllers can command shared equipment once per submitted command')
        result = other('/api/project')
        project = result['body']
        product = next(item for item in project['devices'] if item['id'] == 'own')
        product['config']['password'] = secrets.token_urlsafe(24)
        other('/api/project', project, 'PUT', headers={'If-Match': result['etag']})
        until(lambda: other('/api/devices/own')['body']['state'].get('offline_reason') == 'auth_failed')
        refusal = other('/api/devices/own')['body']
        other('/api/devices/own/command', command, 'POST', expected=(503,))
        assert physical('product2')['writes'] == shared['writes']
        api('/api/devices/product2/command', command)
        api('/api/devices/ordinary/command', {'command': 'set_level', 'params': {'level': 67}})
        until(lambda: api('/api/devices/ordinary')['state'].get('level') == 67)
        assert physical('product2')['counter'] == shared['counter'] + 1
        result = other('/api/project')
        project = result['body']
        next(item for item in project['devices'] if item['id'] == 'own')['config']['password'] = credentials['password']
        other('/api/project', project, 'PUT', headers={'If-Match': result['etag']})
        until(lambda: other('/api/devices/own')['body']['connected'], timeout=90)
        assert physical('product2')['writes'] == shared['writes'] + 1
        other('/api/devices/own/command', command, 'POST')
        final = physical('product2')
        for field in ('counter', 'callbacks', 'writes'):
            assert final[field] == shared[field] + 2
        observation.update(status='passed', credential_refusal=refusal['state'].get('offline_reason'),
                           after_credential_repair=final)
        recorded('Incorrect credentials refuse one controller without interrupting authorized equipment or ordinary TCP control')
    except BaseException as error:
        observation.update(status='failed', error=str(error))
        try:
            observation['secondary_devices'] = [
                {'id': item['id'], 'connected': item['connected'], 'state': item['state']}
                for item in other('/api/devices')['body']['devices']]
        except Exception as diagnostic_error:
            observation['diagnostic_error'] = str(diagnostic_error)
        raise
    finally:
        docker('stop', '--time', '20', secondary)
