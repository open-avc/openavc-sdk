"""Bounded traffic measurement for the isolated network example, not firmware."""
import argparse
import json
import socket
import struct
import time


def run(mode, host, port):
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as connection:
        if mode == 'send':
            for sequence in range(200):
                connection.sendto(struct.pack('!IQ', sequence, time.monotonic_ns()), (host, port))
                time.sleep(.003)
            return
        connection.bind((host, port))
        connection.settimeout(.2)
        print('READY', flush=True)
        deadline = time.monotonic() + 15
        samples = []
        while time.monotonic() < deadline:
            try:
                data = connection.recv(64)
            except TimeoutError:
                if samples and time.monotonic_ns() - samples[-1]['received_ns'] > 2_000_000_000:
                    break
                continue
            sequence, sent = struct.unpack('!IQ', data)
            received = time.monotonic_ns()
            samples.append({'sequence': sequence, 'delay_ms': (received - sent) / 1e6,
                            'received_ns': received})
        seen, duplicates, reordered, highest = set(), 0, 0, -1
        for sample in samples:
            sequence = sample['sequence']
            if sequence in seen:
                duplicates += 1
            elif sequence < highest:
                reordered += 1
            seen.add(sequence)
            highest = max(highest, sequence)
        print(json.dumps({'sent': 200, 'unique_received': len(seen), 'duplicates': duplicates,
                          'out_of_order': reordered, 'samples': samples}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['send', 'receive'])
    parser.add_argument('host')
    parser.add_argument('port', type=int)
    args = parser.parse_args()
    run(args.mode, args.host, args.port)
