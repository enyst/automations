#!/usr/bin/env python3
"""Explicit historical dead-code canary creation/dispatch. No recurring activation."""
import argparse
import json
from pathlib import Path

from deploy_dead_code import ROOT, request_body, retain
from deploy_jev import API, CLOUD, OWNER, Client, DeploymentError, identity, keychain
from export_cloud import Client as DownloadClient, ExportError

TARGET = '5a4071c764a9323c599e70258211b3f67c2aefad'
NAME = 'Canvas dead-code historical canary'
RECEIPT = ROOT / 'evaluations/dead-code-canary-2026-10-13.json'


def canary_body():
    body = request_body()
    body['name'] = NAME
    body['keep_alive'] = True
    body['repos'][0]['ref'] = TARGET
    prompt = body['prompt'].replace('Work against current main.',
        f'This is a historical discovery experiment. Work ONLY at pinned commit {TARGET}; do not fetch code, findings or diffs from newer commits.')
    lines = prompt.splitlines()
    lines = [line for line in lines if not line.startswith('- Before installing/building, inspect open PRs')]
    prompt = '\n'.join(lines) + '\n'
    prompt = prompt.replace('Record the exact main SHA,', 'Verify git rev-parse HEAD equals the pinned target SHA before analysis. Record that SHA,')
    prompt = prompt.replace('Follow the project\'s PR template', 'The draft must prominently disclose this historical target SHA and that it is an experiment, not a proposal based on current main. Follow the project\'s PR template')
    prompt = prompt.replace('"Automation: Canvas dead-code"', '"Automation: Canvas dead-code historical canary"')
    prompt += '\nFor this experiment, do not query other cleanup PRs or use later commits as an answer key. Analyze the pinned source independently. You may open a draft PR with verified removals even if the current branch has since changed; do not rebase it onto newer main. If publication is unavailable, retain the patch and full findings report. Save DEAD_CODE_REPORT.md and DEAD_CODE_PATCH.diff at the workspace root before finishing, and record their absolute paths in your final answer.\n'
    body['prompt'] = prompt
    return body


def owned(cloud, identifier):
    value = cloud.request('GET', API + '/' + identifier)
    if (value.get('name') != NAME or value.get('user_id') != OWNER
            or value.get('org_id') != OWNER or value.get('state') != 'INACTIVE'
            or value.get('enabled') is not False):
        raise DeploymentError('wrong_or_active_canary')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['dry-run', 'create', 'dispatch', 'status'])
    args = parser.parse_args()
    body = canary_body()
    if args.command == 'dry-run':
        print(json.dumps(body)); return
    credential = keychain('OPENHANDS_API_KEY')
    cloud = Client(CLOUD, credential); identity(cloud)
    if args.command == 'create':
        listing = cloud.request('GET', API + '?limit=100')
        if listing.get('total', 0) > 100 or any(a.get('name') == NAME for a in listing.get('automations', [])):
            raise DeploymentError('inspect_inventory_before_create')
        created = cloud.request('POST', API + '/preset/prompt', body)
        identifier = created['id']
        observed = owned(cloud, identifier)
        result = retain(cloud, DownloadClient(credential), identifier, body, observed)
        RECEIPT.parent.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps({'automation_id': identifier, 'target_sha': TARGET,
            'state': 'INACTIVE', 'dispatches': 0, 'public_draft_allowed': True}, indent=2) + '\n')
        print(json.dumps(result)); return
    receipt = json.loads(RECEIPT.read_text()); identifier = receipt['automation_id']
    owned(cloud, identifier)
    runs = cloud.request('GET', API + '/' + identifier + '/runs?limit=10')['runs']
    if args.command == 'dispatch':
        if runs:
            raise DeploymentError('canary_already_dispatched')
        run = cloud.request('POST', API + '/' + identifier + '/dispatch')
        receipt.update({'dispatches': 1, 'run_id': run['id']})
        RECEIPT.write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps({k: run.get(k) for k in ('id', 'status', 'conversation_id', 'sandbox_id')}))
    else:
        print(json.dumps([{k: run.get(k) for k in ('id', 'status', 'conversation_id', 'sandbox_id', 'started_at', 'completed_at')} for run in runs]))


if __name__ == '__main__':
    try:
        main()
    except (DeploymentError, ExportError) as error:
        print(json.dumps({'error': str(error)})); raise SystemExit(1)
