"""Start each BitBake mirror request afresh; retain wget's same-request retries.

BitBake's pinned wget fetcher appends --continue to each mirror request. A
rejected response can remain at the temporary output path, so the next mirror
must not append a range to those bytes. BitBake still verifies the complete
download against the recipe's hash before using it.
"""
import subprocess
import sys


def main(arguments):
    return subprocess.call(['wget', *(arg for arg in arguments if arg != '--continue')])


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
