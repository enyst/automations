"""Publish commit-pinned Jev diagnostics as SmolPaws, without duplicate warnings."""
import hashlib
import json
import urllib.parse

import main as checker
import feature_map

PREFIX = 'Jev-lint warning 🐾\n'


def marker(error):
    path, _ = error['location'].rsplit(':', 1)
    identity = [error['code'], path, error['source_sha256']]
    digest = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
    return '<!-- jev-lint:' + digest + ' -->'


def warning(error, repository, head):
    path, line = error['location'].rsplit(':', 1)
    url = 'https://github.com/' + repository + '/blob/' + head + '/' + urllib.parse.quote(path, safe='/') + '#L' + line
    # All prose comes from our policy, not from untrusted source or Jev output.
    return (PREFIX + '\n`' + error['code'] + '`\n\n' + error['message'] + '\n\n'
            + 'Suggested correction: ' + error['alternative'] + '\n\n'
            + f'Jev signal: {error["signal_probability"]:.2f} (uncalibrated). '
            + '[Checked source](' + url + ').\n\n' + marker(error))


def pages(gh, path):
    results = []
    for page in range(1, 31):
        rows = gh.get(path + f'?per_page=100&page={page}')
        results.extend(rows)
        if len(rows) < 100:
            return results
    raise checker.CheckError('incomplete_comment_listing')


def verify_target(gh, prefix, head):
    pr = gh.get(prefix)
    if (pr.get('user', {}).get('login') != 'enyst' or pr.get('state') != 'open'
            or pr.get('draft') or pr.get('head', {}).get('sha') != head
            or pr.get('base', {}).get('repo', {}).get('private') is not False):
        raise checker.CheckError('publication_target_changed')
    return pr


def publish(gh, report):
    if gh.get('/user').get('login') != 'smolpaws':
        raise checker.CheckError('posting_identity_not_smolpaws')
    repository, number, head = report['repository'], report['pr'], report['head']
    prefix = f'/repos/{repository}/pulls/{number}'
    pr = verify_target(gh, prefix, head)
    from pr_lint import files_for
    files = files_for(gh, prefix, pr['changed_files'])
    changed = {row['filename']: feature_map.changed_lines('+++ b/' + row['filename'] + '\n' + row.get('patch', '')).get(row['filename'], set()) for row in files}
    comments = pages(gh, prefix + '/comments') + pages(gh, f'/repos/{repository}/issues/{number}/comments')
    own_bodies = [c.get('body', '') for c in comments if c.get('user', {}).get('login') == 'smolpaws']
    posted, skipped = [], 0
    for error in report['errors']:
        tag = marker(error)
        if any(tag in body for body in own_bodies):
            skipped += 1
            continue
        path, line = error['location'].rsplit(':', 1)
        body = warning(error, repository, head)
        verify_target(gh, prefix, head)
        if int(line) in changed.get(path, set()):
            result = gh.post(prefix + '/comments', {'body': body, 'commit_id': head,
                                                   'path': path, 'line': int(line), 'side': 'RIGHT'})
            kind = 'inline'
        else:
            # Candidate starts outside the diff (e.g. only its last line changed).
            # A normal comment retains the exact source link; never invent a line.
            result = gh.post(f'/repos/{repository}/issues/{number}/comments', {'body': body})
            kind = 'source_link_comment'
        own_bodies.append(body)
        posted.append({'kind': kind, 'url': result.get('html_url'), 'code': error['code'], 'location': error['location']})
    return {'posted': posted, 'duplicate_warnings_skipped': skipped, 'identity': 'smolpaws'}
