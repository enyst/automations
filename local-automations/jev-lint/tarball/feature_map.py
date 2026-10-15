"""Check changed feature-map prose against the current-behavior policy with Jev."""
import argparse
import json
from pathlib import Path
import re
import subprocess

import main as checker
import focused

RULE = 'MAP_CURRENT_BEHAVIOR'
POLICY = {
    'rule': 'A feature map describes current behavior for the next verification pass, not fix history or older checkouts. Once a fix is established, document its behavior directly. Do not retain superseded behavior, before/after narratives, fix chronology, or verification diaries in the map. Keep it clean and concise. An unresolved known defect, its reproduction and issue reference are allowed. Current prerequisites, supported backend/version differences, source references and executable baseline/fix proof commands are allowed; a PR number alone is not a violation.',
    'question': 'Does this feature-map paragraph violate the current-behavior policy by retaining fix history, superseded behavior, older-checkout descriptions or a verification diary?',
    'alternative': 'State the current expected behavior directly. Remove fix chronology, old-checkout alternatives and dated verification narration. Keep current prerequisites, actionable recipes and unresolved known defects. Preserve historical evidence in the PR/report or Git history instead.',
}


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True)


def changed_lines(diff):
    files = {}
    filename = None
    line = None
    for text in diff.splitlines():
        if text.startswith('+++ '):
            filename = text[4:]
            filename = filename[2:] if filename.startswith('b/') else None
        elif text.startswith('@@ '):
            match = re.search(r'\+(\d+)(?:,\d+)? @@', text)
            line = int(match[1]) if match else None
        elif line is not None:
            if text.startswith('+'):
                if filename:
                    files.setdefault(filename, set()).add(line)
                line += 1
            elif text.startswith(' '):
                line += 1
    return files


def prose(source):
    """Paragraphs outside fenced code; preserve their actual new-file line range."""
    start, lines, fence = None, [], None
    for number, text in enumerate(source.splitlines() + [''], 1):
        marker = re.match(r'^\s*(`{3,}|~{3,})', text)
        if marker:
            if start is not None:
                yield start, number - 1, '\n'.join(lines)
                start, lines = None, []
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            continue
        if fence:
            continue
        if not text.strip():
            if start is not None:
                yield start, number - 1, '\n'.join(lines)
                start, lines = None, []
        elif text.lstrip().startswith('- ') and start is not None:
            yield start, number - 1, '\n'.join(lines)
            start, lines = number, [text]
        else:
            if start is None:
                start = number
            lines.append(text)


def inputs(repo, base, head):
    diff = git(repo, 'diff', '--no-ext-diff', '--no-renames', '--unified=0', base, head, '--',
               '.agents/skills/verify-openhands/references/feature-map/*.md')
    items = []
    for filename, changed in changed_lines(diff).items():
        source = git(repo, 'show', f'{head}:{filename}')
        for first, last, text in prose(source):
            if changed.intersection(range(first, last + 1)):
                items.append({'id': str(len(items)), 'rule': RULE,
                              'location': f'{filename}:{first}', 'code': text})
    return items


def run(repo, base, head, dry_run=False):
    items = inputs(repo, base, head)
    if len(items) > 96:
        raise checker.CheckError('request_budget_exceeded')
    reports = []
    key = None if dry_run or not items else checker.credential()
    for offset in range(0, len(items), 12):
        batch = items[offset:offset + 12]
        request = focused.build(batch)
        if dry_run:
            reports.append({'items': batch, 'request': request})
        else:
            response = checker.evaluate(request, key)
            reports.append({'items': batch, 'response': response, 'errors': focused.result(batch, response)})
    return {'rule': RULE, 'base': git(repo, 'rev-parse', base).strip(),
            'head': git(repo, 'rev-parse', head).strip(), 'candidates': len(items),
            'reports': reports, 'errors': [e for r in reports for e in r.get('errors', [])]}


focused.POLICIES[RULE] = POLICY

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        report = run(args.repo, args.base, args.head, args.dry_run)
        print(json.dumps(report))
        raise SystemExit(2 if report['errors'] else 0)
    except Exception as error:
        code = str(error) if isinstance(error, checker.CheckError) else type(error).__name__
        print(json.dumps({'error': code}))
        raise SystemExit(1)
