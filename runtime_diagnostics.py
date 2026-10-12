"""Selected health observations and a local network snapshot, without raw text."""
from collections import deque
import hashlib
import ipaddress
import os
from pathlib import Path
import socket
import stat

from diagnostic_report import boolean, choice, number
from product_checks import decode


MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_HEALTH_EVENTS = 100
ERROR_TYPES = {'TimeoutError', 'ClientConnectorError', 'ClientConnectorCertificateError',
               'ClientConnectorSSLError', 'ClientOSError', 'ServerDisconnectedError',
               'ServerTimeoutError', 'ConnectionTimeoutError', 'SocketTimeoutError',
               'ClientConnectionError', 'ClientPayloadError', 'OSError', 'ValueError'}


def health_history(paths):
    """Read only explicitly selected regular files; retain input and line order."""
    if not 1 <= len(paths) <= 8:
        raise ValueError('Select between one and eight health log segments.')
    events = deque(maxlen=MAX_HEALTH_EVENTS)
    sources = []
    for index, path in enumerate(paths, 1):
        # Nonblocking open also refuses a FIFO without waiting for its writer.
        fd = os.open(Path(path), os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Select a regular health log file.')
            raw = stream.read(MAX_LOG_BYTES + 1)
        if len(raw) > MAX_LOG_BYTES:
            raise ValueError('A selected health log exceeds 16 MiB.')
        source = {'source': index, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                  'health_records': 0, 'other_records': 0, 'unreadable_records': 0,
                  'incomplete_final_record': bool(raw and not raw.endswith(b'\n'))}
        lines = raw.splitlines()
        if source['incomplete_final_record']:
            lines = lines[:-1]
        for line in lines:
            try:
                item = decode(line)
            except (ValueError, UnicodeError, RecursionError):
                source['unreadable_records'] += 1
                continue
            if type(item) is not dict or item.get('event') != 'core_health':
                source['other_records'] += 1
                continue
            source['health_records'] += 1
            event = {'source': index, 'record': source['health_records'],
                     'responsive': boolean(item.get('responsive')),
                     'previous_responsive': boolean(item.get('previous_responsive'))}
            for key in ('start_unix_time', 'end_unix_time', 'duration_s', 'interval_s', 'timeout_s'):
                event[key] = number(item.get(key))
            status = item.get('status')
            event['http_status'] = status if type(status) is int and 100 <= status <= 599 else None
            error = item.get('error_type')
            event['error_type'] = None if error is None else choice(error, ERROR_TYPES)
            events.append(event)
        sources.append(source)
    return {'coverage': 'selected_recorded_core_health_observations', 'sources': sources,
            'retained_events': list(events),
            'omitted_older_events': sum(item['health_records'] for item in sources) - len(events),
            'continuous_monitoring_verified': False}


def network_snapshot():
    """Inspect this host's interfaces only; no probes, connections or DNS queries."""
    import psutil

    try:
        addresses, statistics = psutil.net_if_addrs(), psutil.net_if_stats()
    except (OSError, psutil.Error) as exc:
        raise ValueError('Local network information could not be read.') from exc
    interfaces = []
    for index, name in enumerate(sorted(set(addresses) | set(statistics)), 1):
        stats = statistics.get(name)
        row = {'interface': index, 'statistics_available': stats is not None,
               'addresses_available': name in addresses,
               'link_up': boolean(stats.isup) if stats else None,
               'mtu_bytes': number(stats.mtu) if stats and stats.mtu > 0 else None,
               'speed_mbps': number(stats.speed) if stats and stats.speed > 0 else None,
               'multicast_capable': None,
               'address_counts': {'ipv4': 0, 'ipv6': 0, 'loopback': 0, 'link_local': 0}}
        flags = getattr(stats, 'flags', None)
        if isinstance(flags, str):
            row['multicast_capable'] = 'multicast' in flags.split(',')
        for address in addresses.get(name, []):
            if address.family not in (socket.AF_INET, socket.AF_INET6):
                continue
            parsed = ipaddress.ip_address(address.address.split('%', 1)[0])
            row['address_counts']['ipv' + str(parsed.version)] += 1
            row['address_counts']['loopback'] += int(parsed.is_loopback)
            row['address_counts']['link_local'] += int(parsed.is_link_local)
        interfaces.append(row)
    return {'coverage': 'collector_host_interfaces', 'interfaces': interfaces,
            'connectivity_verified': False, 'discovery_verified': False}
