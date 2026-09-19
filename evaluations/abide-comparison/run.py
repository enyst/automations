#!/usr/bin/env python3
"""Opt-in, code-only Jev comparison. Never executes fixtures or writes to GitHub."""
from __future__ import annotations
import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import types
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'sources/jev-fast-audit'))
# Freeze the pre-experiment code and validator even after live code changes.
BASELINE_COMMIT = "fe5a5d32af5f343f9204b173ee5f1966db2be8bf"
BASELINE_SOURCE = (HERE / "baseline_audit.py").read_bytes()
if hashlib.sha256(BASELINE_SOURCE).hexdigest() != "4fd7d1261284f456861778b3577f207b62aacf3b4f597cbcae120f2b60e0e0a3":
    raise ValueError("baseline_source_changed")
audit = types.ModuleType("baseline_audit")
exec(compile(BASELINE_SOURCE, "baseline_audit.py", "exec"), audit.__dict__)
import main as runtime
sys.path.insert(0, str(ROOT / 'scripts'))
import deploy_jev as deploy


def candidate_questions(state, rules):
    questions = audit.questions_for(state)
    for rule in rules:
        key = rule['id']
        claim = audit.RISKS[key][1]
        questions[key]['instructions'] = audit._POLICY + rule['instructions']
        questions[key]['criteria'] = {
            'true': claim + ' Example: ' + rule['criteria']['true'],
            'false': 'The supplied change does not introduce this specific problem. Examples: ' + rule['criteria']['false'],
        }
    return questions


def payload_for(state, variant, rules):
    state = copy.deepcopy(state)
    questions = audit.questions_for(state) if variant == 'baseline' else candidate_questions(state, rules)
    payload = {'model': audit.MODEL, 'state': state, 'questions': questions}
    for _ in range(8):
        size = len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())
        if state['coverage']['serialized_bytes'] == size:
            break
        state['coverage']['serialized_bytes'] = size
    return payload


def fixture_state(case, number):
    source = case['source']
    lines = source.splitlines(keepends=True)
    patch = '@@ -0,0 +1,' + str(len(lines)) + ' @@\n' + ''.join('+' + line for line in lines)
    pr = {'html_url': f'https://github.com/enyst/automations/pull/{number}',
          'title': case['title'], 'body': case['body'], 'state': 'open',
          'head': {'sha': 'a' * 40}, 'base': {'sha': 'b' * 40}}
    return audit.build_context(pr, [{'filename': case['filename'], 'status': 'added', 'patch': patch}],
                               {case['filename']: {'base': None, 'head': source}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Make classifier calls using the existing authorized TypeSafe key')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    candidate = json.loads((HERE / 'candidate.json').read_text())
    cases = json.loads((HERE / 'cases.json').read_text())
    rules = candidate['rules']
    assert {r['id'] for r in rules} == set(audit.RISKS)
    jobs = []
    for index, case in enumerate(cases, 1):
        state = fixture_state(case, index)
        assert state['coverage']['complete']
        for variant in ('baseline', 'candidate'):
            jobs.append((case['id'], variant, state, case['expected']))
    hashes = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in ('candidate.json', 'cases.json', 'run.py')}
    hashes['baseline_audit.py'] = hashlib.sha256(BASELINE_SOURCE).hexdigest()
    report = {'created_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'model': audit.MODEL,
              'hashes': hashes, 'job_count': len(jobs), 'runs': [],
              'baseline_commit': BASELINE_COMMIT,
              'method': 'Same evidence/model/policy; candidate changes only Noul wording and true/false examples. Evidence and headline questions unchanged. Expected labels are never sent.'}
    print(json.dumps({k: v for k, v in report.items() if k != 'runs'}), flush=True)
    if not args.run:
        return
    token = deploy.keychain('TYPESAFE_API_KEY')
    def evaluate(job):
        identifier, variant, state, expected = job
        payload = payload_for(state, variant, rules)
        record = {'case': identifier, 'variant': variant, 'expected': expected,
                  'request_sha256': hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()}
        start = time.monotonic()
        try:
            result = runtime.API(runtime.TYPESAFE, token).request('/v1/systemone', 'POST', payload)
            record.update(model=result.get('model'), usage=result.get('usage'), answers=result.get('answers'))
            audit.validate_answers(result, payload['questions'])
            record['status'] = 'valid'
        except (runtime.AuditError, audit.AuditValidationError) as error:
            record.update(status='failed', error=str(error))
        record['latency_ms'] = round((time.monotonic() - start) * 1000)
        return record
    with ThreadPoolExecutor(max_workers=3) as pool:
        for task in as_completed([pool.submit(evaluate, job) for job in jobs]):
            record = task.result()
            report['runs'].append(record)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps({'case': record['case'], 'variant': record['variant'], 'status': record['status'],
                              'signals': {key: record.get('answers', {}).get(key, {}).get('noul') for key in record['expected'] or ['secretDisclosure']}}), flush=True)


if __name__ == '__main__':
    main()
