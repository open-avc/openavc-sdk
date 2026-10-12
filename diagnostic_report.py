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


def summarize(status=None, checks=None, resources=None, server_bundle=None, health_logs=(), network=False):
    report = {'format': 1, 'collected': [], 'findings': [],
              'omitted': ['credentials', 'keys', 'network addresses', 'paths', 'product identity and labels',
                          'raw errors and logs', 'projects', 'command parameters', 'hardware state values',
                          'process IDs, arguments and environment']}
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
