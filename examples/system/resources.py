"""Short resource failures confined to the disposable product example."""

import json


QUEUE_CHECK = r'''
import base64,json,pathlib,secrets,ssl,urllib.error,urllib.request
login=json.loads(pathlib.Path('/data/login.json').read_text())
headers={'Authorization':'Basic '+base64.b64encode((login['username']+':'+login['password']).encode()).decode(),
         'X-OpenAVC-Client':secrets.token_hex(16),'Content-Type':'application/json'}
context=ssl.create_default_context(cafile='/data/ca.pem')
bind=json.loads(pathlib.Path('/data/product.json').read_text())['equipment_bind']
host='[::1]' if ':' in bind else '127.0.0.1'
def request(path,body=None):
    req=urllib.request.Request('https://'+host+':9443'+path,headers=headers,
        data=json.dumps(body).encode() if body is not None else None)
    try: response=urllib.request.urlopen(req,context=context,timeout=5)
    except urllib.error.HTTPError as error: response=error
    with response: return {'status':response.status,'body':json.load(response)}
description=request('/v1/description')['body']
outcomes=[]
for number in range(1,4):
    outcomes.append(request('/v1/command',{'session':description['session'],'id':number,'command':'increment','params':{}}))
health=request('/health')
print(json.dumps({'requests':outcomes,'health':health}),flush=True)
'''

FILL = r'''
import errno,json,os,pathlib
path=pathlib.Path('/data/openavc/resource-fill')
mounts=pathlib.Path('/proc/self/mountinfo').read_text().splitlines()
assert any(' /data/openavc ' in row and ' - tmpfs ' in row for row in mounts)
observed=os.statvfs(path.parent)
assert observed.f_blocks*observed.f_frsize <= 16*1024*1024
written=0
try:
    with path.open('wb',buffering=0) as stream:
        while True: written += stream.write(b'x'*65536)
except OSError as error:
    assert error.errno==errno.ENOSPC
    print(json.dumps({'errno':error.errno,'bytes_written':written,
        'free_bytes':os.statvfs(path.parent).f_bavail*observed.f_frsize}))
'''

FAILED_SAVE = r'''
import hashlib,json,pathlib,urllib.error,urllib.request
path=pathlib.Path('/data/openavc/management.json')
before=path.read_bytes()
headers={'Authorization':'Bearer '+pathlib.Path('/data/management.token').read_text(),'Content-Type':'application/json'}
url='http://127.0.0.1:9081/oem/settings'
current=json.load(urllib.request.urlopen(urllib.request.Request(url,headers=headers)))
request=urllib.request.Request(url,headers=headers,data=json.dumps({
    'revision':current['revision'],'field':'remote_access','enabled':False}).encode())
try: response=urllib.request.urlopen(request,timeout=10)
except urllib.error.HTTPError as error: response=error
with response: result={'status':response.status,'body':json.load(response)}
after=path.read_bytes()
result.update(policy_unchanged=before==after,policy_sha256=hashlib.sha256(after).hexdigest())
print(json.dumps(result))
'''


def check_resources(layout, report, *, api, docker, physical, until, recorded, product_status):
    peer = layout['products']['product2']['container']
    observation = {}
    report['observations']['resources'] = observation
    before = physical('product2')
    physical('product2', 'delay 5000')
    queued = json.loads(docker('exec', peer, 'python', '-c', QUEUE_CHECK))
    observation['queue'] = queued
    assert [row['status'] for row in queued['requests']] == [202, 202, 429]
    assert queued['requests'][2]['body']['error'] == 'busy'
    assert queued['health']['status'] == 200 and queued['health']['body']['pending'] == 2
    api('/api/devices/ordinary/command', {'command': 'set_level', 'params': {'level': 61}})
    until(lambda: api('/api/devices/ordinary')['state'].get('level') == 61)
    finished = until(lambda: state if (state := physical('product2'))['counter'] == before['counter'] + 2 else None)
    for field in ('counter', 'callbacks', 'writes'):
        assert finished[field] == before[field] + 2
    observation['queue'].update(hardware_before=before, hardware_after=finished)
    recorded('The full command queue refuses excess work while health and ordinary control remain available')
    try:
        observation['storage'] = json.loads(docker('exec', peer, 'python', '-c', FILL))
        assert observation['storage']['free_bytes'] == 0
        failed = json.loads(docker('exec', peer, 'python', '-c', FAILED_SAVE))
        observation['failed_save'] = failed
        assert failed['status'] == 507 and failed['policy_unchanged']
        assert failed['body']['state']['settings_valid']
        assert failed['body']['state']['remote_access']
        api('/api/devices/product2/command', {'command': 'increment', 'params': {}})
        under_pressure = physical('product2')
        assert under_pressure['counter'] == finished['counter'] + 1
        assert under_pressure['writes'] == finished['writes'] + 1
        observation['hardware_with_full_storage'] = under_pressure
        recorded('Full isolated storage refuses a settings save without changing active policy or preventing hardware control')
    finally:
        docker('exec', peer, 'python', '-c', "from pathlib import Path; Path('/data/openavc/resource-fill').unlink(missing_ok=True)")
    assert product_status('product2', 'remote_access', False)['remote_access'] is False
    assert product_status('product2', 'remote_access', True)['remote_access'] is True
    until(lambda: api('/api/devices/product2')['connected'])
    assert physical('product2')['writes'] == under_pressure['writes']
    api('/api/devices/product2/command', {'command': 'increment', 'params': {}})
    final = physical('product2')
    assert final['writes'] == under_pressure['writes'] + 1
    observation.update(status='passed', hardware_after_recovery=final)
    recorded('After freeing storage, normal settings and a fresh command work without replay')
