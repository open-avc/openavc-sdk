"""Probe only the isolated example's equipment endpoint from a product container."""
import argparse
import json
from pathlib import Path
import ssl
import sys
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from product_checks import CheckFailed, Client  # noqa: E402


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='product2')
    parser.add_argument('--path', default='/v1/description')
    parser.add_argument('--body')
    parser.add_argument('--wrong-login', action='store_true')
    parser.add_argument('--wrong-trust', action='store_true')
    args = parser.parse_args()
    credentials = json.loads(Path('/data/login.json').read_text())
    if args.wrong_login:
        credentials['password'] += '-incorrect'
    client = Client('https://' + args.host + ':9443', credentials,
                    None if args.wrong_trust else '/data/trust.pem')
    try:
        status, result = client.request(args.path, json.loads(args.body) if args.body else None)
    except CheckFailed as error:
        cause = error.__cause__
        if (isinstance(cause, urllib.error.URLError)
                and isinstance(cause.reason, ssl.SSLCertVerificationError)):
            print(json.dumps({'tls_refused': True}))
        else:
            raise
    else:
        print(json.dumps({'status': status, 'response': result}))
