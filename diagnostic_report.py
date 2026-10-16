"""Allowlisted diagnostic summary; raw product values never pass through."""
import math
import re

from openavc_oem.state import FLAGS


def boolean(value):
    return value if type(value) is bool else None


def choice(value, choices):
    return value if type(value) is str and value in choices else 'unknown'


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def object_value(value):
    return value if type(value) is dict else {}


def digest(value):
    return value if type(value) is str and re.fullmatch('[0-9a-f]{64}', value) else None


def summarize(status=None, checks=None, resources=None, server_bundle=None, health_logs=(), network=False, environment=None):
    report = {'format': 1, 'collected': [], 'findings': [],
              'omitted': ['credentials', 'keys', 'network addresses', 'paths', 'product identity and labels',
                          'raw errors and logs', 'projects', 'command parameters', 'hardware state values',
                          'process IDs, arguments and environment']}
    if environment is not None:
        source = object_value(environment)
        if source.get('format') != 1 or source.get('coverage') != 'build_prerequisites':
            raise ValueError('Use a report produced by doctor.py from the same SDK revision.')
        known = {'Python', 'Development host', 'Delivery form', 'Firmware toolchain file',
                 'Firmware toolchain source build', 'Prebuilt library platform', 'Supplied SDK files',
                 'cmake', 'cc', 'c++', 'openssl', 'OpenSSL development directory',
                 'C11, supplied SDK, threads and OpenSSL 3 development files',
                 'C11/C++17, threads and OpenSSL 3 development files'}
        rows = source.get('checks')
        if type(rows) is not list:
            raise ValueError('The environment report has no check list.')
        host = object_value(source.get('host'))
        python = host.get('python')
        probe = object_value(source.get('compile_probe'))
        target = object_value(probe.get('target'))
        summary = {
            'status': choice(source.get('status'), {'prerequisites-verified', 'needs-attention', 'error'}),
            'delivery': choice(source.get('delivery'), {'source', 'supplied-c-runtime'}),
            'form': choice(source.get('form'), {'direct', 'service'}),
            'doctor_sha256': digest(source.get('doctor_sha256')),
            'toolchain_sha256': digest(object_value(source.get('toolchain')).get('sha256')),
            'host': {'os': choice(host.get('os'), {'Linux', 'Darwin', 'Windows'}),
                     'machine': choice(host.get('machine'), {'arm64', 'aarch64', 'x86_64', 'AMD64'}),
                     'python': python if type(python) is str and re.fullmatch(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(?:(?:a|b|rc)[0-9]{1,3})?', python) else None},
            'compile_probe': choice(probe.get('status'), {'not-run', 'failed', 'passed'}),
            'target': {'system': choice(target.get('system'), {'Linux', 'Darwin', 'Windows'}),
                       'processor': choice(target.get('processor'), {'arm64', 'aarch64', 'x86_64', 'AMD64'}),
                       'cross_compiling': {'TRUE': True, 'FALSE': False}.get(choice(target.get('cross_compiling'), {'TRUE', 'FALSE'}))},
            'checks': [], 'unrecognized_checks': 0,
            'target_execution_verified': False,
        }
        for row in rows:
            row = object_value(row)
            name = row.get('check')
            if type(name) is str and name in known:
                summary['checks'].append({'check': name, 'status': choice(row.get('status'), {'passed', 'needs-attention'})})
            else:
                summary['unrecognized_checks'] += 1
        report['environment'] = summary
        report['collected'].append('build_environment')
        if summary['status'] != 'prerequisites-verified':
            report['findings'].append('Build prerequisites need attention. Read the local doctor report for compiler and dependency details.')
        if summary['unrecognized_checks']:
            report['findings'].append('Some environment checks were not recognized. Use matching SDK tools and retain the original report.')
    if status is not None:
        source = object_value(object_value(status).get('status'))
        if not source:
            raise ValueError('Use the JSON downloaded by product management under Download diagnostics.')
        report['collected'].append('product_status')
        equipment = object_value(source.get('equipment'))
        report['product'] = {key: boolean(source.get(key)) for key in (
            'server_installed', 'server_running', 'server_responsive', *FLAGS,
            'settings_valid', 'storage_cleanup_pending')}
        report['product']['transition'] = choice(source.get('transition'), {'idle', 'starting', 'applying'})
        output = object_value(source.get('process_logs'))
        report['product']['process_logs'] = {
            key: output[key] if type(output.get(key)) is int and output[key] >= 0 else None
            for key in ('dropped_bytes', 'queued_chunks')}
        if report['product']['process_logs']['dropped_bytes']:
            report['findings'].append('Some process output was not retained. Check local storage and logging verbosity.')
        report['equipment'] = {key: boolean(equipment.get(key)) for key in (
            'reserved_locally', 'discovery_active', 'remote_access_active')}
        if source.get('settings_valid') is False:
            report['findings'].append('Product settings require repair. Check local storage and use product management to repair settings.')
        if source.get('storage_cleanup_pending') is True:
            report['findings'].append('Settings cleanup is unconfirmed. Retry settings through product management before restarting.')
        if source.get('server') is True and source.get('server_running') is False:
            report['findings'].append('OpenAVC is requested but is not running. Check local startup diagnostics.')
        if equipment.get('reserved_locally') is True and (
                equipment.get('discovery_active') is True or equipment.get('remote_access_active') is True):
            report['findings'].append('Remote availability conflicts with the local hardware reservation. Check the product integration.')
    if checks is not None:
        source = object_value(checks)
        if source.get('format') != 1 or source.get('coverage') != 'protocol_and_reported_state':
            raise ValueError('Use report.json produced by check-product.py or try-example.py --checks.')
        report['collected'].append('product_checks')
        report['checks'] = {'status': choice(source.get('status'), {'passed', 'failed', 'running'}),
                            'model_canonical_sha256': digest(source.get('model_canonical_sha256')),
                            'plan_canonical_sha256': digest(source.get('plan_canonical_sha256')),
                            'physical_hardware_verified': False}
        commands = source.get('commands')
        report['checks']['commands'] = {state: sum(object_value(item).get('status') == state for item in commands)
            for state in ('dispatching', 'observing', 'checking_duplicate', 'passed')} if type(commands) is list else None
        refusals = source.get('refusals')
        report['checks']['refusals'] = {state: sum(object_value(item).get('status') == state for item in refusals)
            for state in ('dispatching', 'observing', 'passed')} if type(refusals) is list else None
        if 'reconnect' in source:
            reconnect = object_value(source['reconnect'])
            report['checks']['reconnect'] = {
                'status': choice(reconnect.get('status'), {'waiting_for_disconnect', 'waiting_for_recovery', 'passed'}),
                'timeout': number(reconnect.get('timeout')),
                'unavailable_after_seconds': number(reconnect.get('unavailable_after_seconds')),
                'recovered_after_seconds': number(reconnect.get('recovered_after_seconds')),
            }
        if source.get('status') != 'passed':
            report['findings'].append('Product checks did not finish successfully. Read the private report before sending another command.')
    if resources is not None:
        source = object_value(resources)
        if source.get('format') != 1 or source.get('coverage') != 'selected_local_processes_and_observed_descendants':
            raise ValueError('Use report.json produced by profile-product.py.')
        report['collected'].append('resource_measurement')
        report['resources'] = {'status': choice(source.get('status'), {'complete', 'failed', 'running'}),
                               'elapsed_seconds': number(source.get('elapsed_seconds')),
                               'samples': number(source.get('samples')),
                               'observed_peak': {key: number(object_value(source.get('peak')).get(key))
                                                 for key in ('rss_bytes', 'cpu_percent', 'threads', 'descriptors')}}
        if source.get('status') != 'complete':
            report['findings'].append('Resource measurement did not finish. Inspect the private report and process supervisor.')
    if server_bundle is not None:
        from bundle_diagnostics import inspect_bundle
        report['server_bundle'] = inspect_bundle(server_bundle)
        report['collected'].append('offline_server_bundle')
    if health_logs:
        from runtime_diagnostics import health_history
        report['health'] = health_history(health_logs)
        report['collected'].append('recorded_server_health')
        if any(source['unreadable_records'] or source['incomplete_final_record']
               for source in report['health']['sources']):
            report['findings'].append('Some selected health records could not be read completely. Retain the original logs for local inspection.')
    if network:
        from runtime_diagnostics import network_snapshot
        report['network'] = network_snapshot()
        report['collected'].append('collector_network_interfaces')
    if not report['collected']:
        raise ValueError('Select at least one diagnostic source.')
    return report
