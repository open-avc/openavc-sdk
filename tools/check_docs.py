"""Check local inline links in the README and manufacturer guides, without network access."""
import argparse
from collections import Counter
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'\[[^\]\n]*\]\(([^\s)]+)\)')


def prose(text):
    """Keep source line numbers while excluding fenced examples."""
    fence = None
    for line in text.splitlines():
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            yield ''
        else:
            yield line if fence is None else ''


def anchors(path):
    found, counts = set(), Counter()
    for line in prose(path.read_text(encoding='utf-8')):
        heading = re.match(r'^ {0,3}#{1,6}\s+(.+?)\s*#*$', line)
        if heading:
            label = LINK.sub(lambda match: match[0].split(']')[0][1:], heading[1])
            slug = re.sub(r'[^\w -]', '', label.lower()).replace(' ', '-')
            found.add(slug + ('-' + str(counts[slug]) if counts[slug] else ''))
            counts[slug] += 1
    return found


def check(root):
    root = root.resolve()
    pages = [root / 'README.md', *sorted((root / 'docs').rglob('*.md'))]
    if len(pages) < 2:
        raise ValueError('Select the SDK root containing README.md and docs/.')
    errors, total, known_anchors = [], 0, {}
    for page in pages:
        for line_number, line in enumerate(prose(page.read_text(encoding='utf-8')), 1):
            for match in LINK.finditer(line):
                link = urlsplit(match[1])
                if link.scheme or link.netloc:
                    continue
                total += 1
                target = (page.parent / unquote(link.path)).resolve() if link.path else page
                reason = None
                if not target.is_relative_to(root):
                    reason = 'target is outside the SDK'
                elif not target.is_file():
                    reason = 'file is missing'
                elif link.fragment and target.suffix == '.md':
                    if target not in known_anchors:
                        known_anchors[target] = anchors(target)
                    if unquote(link.fragment) not in known_anchors[target]:
                        reason = 'heading is missing'
                if reason:
                    errors.append(f'{page.relative_to(root)}:{line_number}: {match[1]}: {reason}')
    if errors:
        raise ValueError('\n'.join(errors))
    print(f'Checked {total} local inline links in {len(pages)} documentation files.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='Source checkout or extracted SDK package')
    try:
        check(parser.parse_args().root)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')
