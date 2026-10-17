"""Generate the offline C declaration reference from the supplied public headers."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADERS = [('adapter.h', 'Hardware adapter'), ('outcome.h', 'Command outcomes'),
           ('endpoint.h', 'Advanced endpoint'), ('protocol.h', 'Protocol constants')]


def render(root):
    text = ['# C declarations', '',
            'Use the [hardware adapter guide](adapter-api.md) for ownership, threading,',
            'readback and shutdown requirements. These declarations match the public',
            'headers supplied with this SDK. Include the headers in your application;',
            'do not copy declarations from this page.', '',
            'Your generated `commands.h` supplies the callbacks and state publishers',
            'for your own [product model](product-model.md). Regenerate that header',
            'when changing the model.', '']
    for name, title in HEADERS:
        source = root / 'sdk/include' / name
        text += ['## ' + title, '', f'Header: [{name}](../sdk/include/{name})', '',
                 '```c', source.read_text(encoding='utf-8').rstrip(), '```', '']
    return '\n'.join(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT,
                        help='Source checkout or extracted SDK package')
    parser.add_argument('--check', action='store_true', help='Refuse a missing or stale reference')
    args = parser.parse_args()
    output = args.root / 'docs/c-declarations.md'
    expected = render(args.root)
    if args.check:
        if not output.is_file() or output.read_text(encoding='utf-8') != expected:
            parser.exit(1, 'C declaration reference is stale. Run python3 tools/generate_api_reference.py in the source checkout.\n')
        print('C declaration reference matches the supplied headers.')
    else:
        output.write_text(expected, encoding='utf-8')
        print('Generated ' + str(output))
