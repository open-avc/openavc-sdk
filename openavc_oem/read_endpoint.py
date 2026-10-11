"""Read the maintained server's listener selection in its own Python environment."""

import json
from contextlib import redirect_stdout
from pathlib import Path
import socket
import sys

# Core may report first-run configuration while resolving the listener. Keep
# diagnostics on stderr and reserve stdout for this process's JSON result.
with redirect_stdout(sys.stderr):
    from openavc import config, listen
    from openavc.system_config import get_system_config

    plan = listen.start()
    port = config.TLS_PORT if config.TLS_ENABLED else config.HTTP_PORT
    addresses = dict.fromkeys((family, address) for host in plan.hosts
        for family, _, _, _, address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE))
    certificate = Path(config.TLS_CERT_FILE).resolve() if config.TLS_CERT_FILE else get_system_config().data_dir / 'tls/server.crt'
print(json.dumps({'bindings': [{'family': family, 'address': address} for family, address in addresses],
                  'scheme': 'https' if config.TLS_ENABLED else 'http', 'port': port,
                  'certificate': str(certificate), 'loopback': plan.local_only}))
