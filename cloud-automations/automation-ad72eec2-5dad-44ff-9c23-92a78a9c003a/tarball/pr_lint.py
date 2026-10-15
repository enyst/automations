"""PR lint: author-gated classification and optional SmolPaws publication."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

import main as checker
import focused
import feature_map
from helpers import fire_callback, get_secret

ALLOWED_AUTHOR = 'enyst'


class GitHub:
    def __init__(self, key):
        self.key = key

    def get(self, path):
        return self.request('GET', path)

    def post(self, path, body):
        return self.request('POST', path, body)

    def request(self, method, path, body=None):
        if path != '/user' and not path.startswith('/repos/'):
            raise checker.CheckError('invalid_github_path')
        req = urllib.request.Request('https://api.github.com' + path,
            data=json.dumps(body).encode() if body is not None else None, method=method, headers={
            'Authorization': 'Bearer ' + self.key, 'Accept': 'application/vnd.github+json',
            'User-Agent': 'enyst-jev-lint', 'Content-Type': 'application/json'})
        try:
            with urllib.request.build_opener(checker.NoRedirect).open(req, timeout=30) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise checker.CheckError('github_response_too_large')
            return json.loads(raw)
        except urllib.error.HTTPError as error:
            raise checker.CheckError('github_http_' + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise checker.CheckError('github_network_failure') from None


def target(repository, number, event=None):
    # The event selects a target only; it never authorizes its asserted author.
    if event and 'trigger' in event:
        event = event.get('event') or {}
    if event:
        if event.get('action') not in (None, 'opened', 'synchronize', 'ready_for_review'):
            return None
        repository = event.get('repository', {}).get('full_name')
        number = event.get('pull_request', {}).get('number')
    if repository is None and number is None:
        return None
    if not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise checker.CheckError('invalid_repository')
    if type(number) is not int or number < 1:
        raise checker.CheckError('invalid_pr_number')
    return repository, number


def files_for(gh, prefix, expected):
    rows = []
    for page in range(1, 31):
        batch = gh.get(prefix + f'/files?per_page=100&page={page}')
        rows.extend(batch)
        if len(batch) < 100:
            break
    if len(rows) != expected:
        raise checker.CheckError('incomplete_pr_file_listing')
    return rows


def run(gh, repository, number, credential=checker.credential, evaluate=checker.evaluate):
    prefix = f'/repos/{repository}/pulls/{number}'
    pr = gh.get(prefix)
    author = pr.get('user', {}).get('login')
    if author != ALLOWED_AUTHOR:
        return {'status': 'skipped', 'reason': 'author_not_enyst', 'repository': repository, 'pr': number}
    if pr.get('state') != 'open' or pr.get('draft'):
        return {'status': 'skipped', 'reason': 'not_open_ready_pr', 'repository': repository, 'pr': number}
    if pr.get('base', {}).get('repo', {}).get('private') is not False:
        return {'status': 'skipped', 'reason': 'public_repository_required', 'repository': repository, 'pr': number}
    head = pr['head']['sha']
    if not re.fullmatch(r'[a-f0-9]{40}', head):
        raise checker.CheckError('invalid_head_sha')
    rows = files_for(gh, prefix, pr['changed_files'])
    items, missing = [], []
    for row in rows:
        filename = row['filename']
        is_map = filename.startswith('.agents/skills/verify-openhands/references/feature-map/') and filename.endswith('.md')
        if row['status'] == 'removed' or not (is_map or filename.endswith('.py')):
            continue
        patch = row.get('patch')
        if not patch:
            missing.append(filename)
            continue
        lines = feature_map.changed_lines('+++ b/' + filename + '\n' + patch).get(filename, set())
        if not lines:
            continue
        file = gh.get(f'/repos/{repository}/contents/' + urllib.parse.quote(filename, safe='/') + '?ref=' + head)
        if file.get('encoding') != 'base64' or file.get('size', 0) > 250_000:
            missing.append(filename)
            continue
        source = base64.b64decode(file['content'], validate=False).decode('utf-8')
        if is_map:
            candidates = [{'rule': feature_map.RULE, 'location': f'{filename}:{first}', 'code': text}
                          for first, last, text in feature_map.prose(source)
                          if lines.intersection(range(first, last + 1))]
        else:
            candidates = focused.inputs({filename: source})
            candidates = [item for item in candidates if lines.intersection(range(
                int(item['location'].rsplit(':', 1)[1]),
                int(item['location'].rsplit(':', 1)[1]) + len(item['code'].splitlines())))]
        for item in candidates:
            item['id'] = str(len(items))
            items.append(item)
    if missing:
        raise checker.CheckError('incomplete_relevant_source')
    if len(items) > 96:
        raise checker.CheckError('request_budget_exceeded')
    # Verify the target again before paid checks; never trust webhook author data.
    current = gh.get(prefix)
    if current.get('user', {}).get('login') != ALLOWED_AUTHOR or current['head']['sha'] != head or current.get('state') != 'open' or current.get('draft'):
        raise checker.CheckError('pr_changed_before_classification')
    key = credential() if items else None
    reports = []
    for offset in range(0, len(items), 12):
        batch = items[offset:offset + 12]
        response = evaluate(focused.build(batch), key)
        reports.append({'items': [{k: item[k] for k in ['id', 'rule', 'location']} for item in batch],
                        'response': response, 'errors': focused.result(batch, response)})
    current = gh.get(prefix)
    if current.get('user', {}).get('login') != ALLOWED_AUTHOR or current['head']['sha'] != head:
        raise checker.CheckError('pr_changed_during_classification')
    errors = [error for report in reports for error in report['errors']]
    return {'status': 'lint_findings' if errors else 'no_emitted_findings',
            'repository': repository, 'pr': number, 'head': head, 'author': ALLOWED_AUTHOR,
            'candidates': len(items), 'reports': reports, 'errors': errors,
            'publication': 'run_logs_only'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository')
    parser.add_argument('--pr', type=int)
    parser.add_argument('--no-publish', action='store_true', help='Classify and log only; never fetch posting credentials.')
    args = parser.parse_args()
    try:
        event = json.loads(os.environ['AUTOMATION_EVENT_PAYLOAD']) if os.environ.get('AUTOMATION_EVENT_PAYLOAD') else None
        selected = target(args.repository, args.pr, event)
        if selected is None:
            print(json.dumps({'status': 'skipped', 'reason': 'explicit_pr_target_required'}))
        else:
            report = run(GitHub(os.environ.get('GITHUB_TOKEN') or get_secret('github_token')), *selected)
            if report.get('errors') and not args.no_publish:
                from publish import publish
                report['publication'] = publish(GitHub(get_secret('SMOLPAWS_TOKEN')), report)
            print(json.dumps(report))
        fire_callback('COMPLETED')
    except Exception as error:
        code = str(error) if isinstance(error, checker.CheckError) else type(error).__name__
        print(json.dumps({'error': code}))
        fire_callback('FAILED', code)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
