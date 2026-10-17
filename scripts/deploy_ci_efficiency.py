#!/usr/bin/env python3
"""Create one inactive, read-only Cloud CI audit; dispatch only on explicit command."""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

from deploy_jev import API, CLOUD, OWNER, Client, DeploymentError, bundle, identity, keychain, secret_names
from export_cloud import Client as ExportClient, MAX_ARCHIVE

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'cloud-automations/automation-09ec7d31-cd3a-42b4-8133-d1a7c41f946e'
NAME = 'CI efficiency audit - OpenHands (read-only trial)'


def definition():
    value = json.loads((PACKAGE / 'automation.yaml').read_text())
    if (value.get('name') != NAME or value.get('state') != 'INACTIVE'
            or value.get('enabled') is not False or value.get('keep_alive') is not False
            or value.get('timeout') != 1500
            or value.get('model') != 'transpile-deepseek-pro-native'
            or value.get('trigger') != {'type': 'cron', 'schedule': '0 0 1 1 *', 'timezone': 'UTC'}):
        raise DeploymentError('trial_definition_changed')
    return value


def owned(cloud, identifier):
    identifier = str(uuid.UUID(identifier))
    value = cloud.request('GET', API + '/' + identifier)
    if (value.get('name') != NAME or value.get('user_id') != OWNER or value.get('org_id') != OWNER
            or value.get('state') != 'INACTIVE' or value.get('enabled') is not False):
        raise DeploymentError('wrong_or_active_trial')
    return value


def verify(cloud, identifier, archive):
    value = owned(cloud, identifier)
    desired = definition()
    for key, expected in desired.items():
        observed = value.get(key)
        if key == 'trigger':
            observed = {k: observed.get(k) for k in expected} if isinstance(observed, dict) else observed
        if observed != expected:
            raise DeploymentError('readback_mismatch:' + key)
    downloaded = ExportClient(cloud.credential).get(API + '/' + identifier + '/tarball', MAX_ARCHIVE)
    if downloaded != archive:
        raise DeploymentError('bundle_readback_mismatch')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['dry-run', 'create', 'dispatch', 'status'])
    parser.add_argument('--automation-id')
    args = parser.parse_args()
    desired = definition()
    archive, files = bundle(PACKAGE / 'tarball')
    checksum = hashlib.sha256(archive).hexdigest()
    if args.command == 'dry-run':
        print(json.dumps({'files': files, 'sha256': checksum, 'network_requests': 0, 'state': desired['state']}))
        return
    cloud = Client(CLOUD, keychain('OPENHANDS_API_KEY'))
    identity(cloud)
    archive, files = bundle(PACKAGE / 'tarball', (cloud.credential,))
    if args.command == 'create':
        listing = cloud.request('GET', API + '?limit=100')
        if listing.get('total', 101) > 100 or any(a['name'] == NAME for a in listing.get('automations', [])):
            raise DeploymentError('inspect_inventory_before_create')
        user = cloud.request('GET', '/api/v1/users/me')
        profiles = (user.get('llm_profiles') or {}).get('profiles', {})
        if profiles.get(desired['model'], {}).get('base_url') != 'https://api.deepseek.com':
            raise DeploymentError('native_model_missing')
        if 'SMOLPAWS_TOKEN' not in secret_names(cloud):
            raise DeploymentError('bot_secret_missing')
        upload = cloud.request('POST', API + '/uploads?name=CI-efficiency-trial', archive, 'application/gzip')
        if upload.get('status') != 'COMPLETED':
            raise DeploymentError('upload_incomplete')
        desired['tarball_path'] = upload['tarball_path']
        validated = cloud.request('POST', API + '/validate', {'endpoint': '/v1', 'draft': {k: v for k, v in desired.items() if k not in {'prompt', 'preset_metadata'}}})
        if validated.get('valid') is not True:
            raise DeploymentError('validation_failed')
        created = cloud.request('POST', API, desired)
        # Retain the ID even if a subsequent readback fails. Never create twice.
        print(json.dumps({'created_id': created['id']}), flush=True)
        observed = verify(cloud, created['id'], archive)
        print(json.dumps({'id': observed['id'], 'state': observed['state'], 'sha256': checksum, 'files': files}))
    else:
        if not args.automation_id:
            raise DeploymentError('automation_id_required')
        observed = verify(cloud, args.automation_id, archive)
        runs = cloud.request('GET', API + '/' + observed['id'] + '/runs?limit=10')['runs']
        if args.command == 'dispatch':
            if any(r['status'] in {'PENDING', 'RUNNING'} for r in runs):
                raise DeploymentError('trial_already_running')
            run = cloud.request('POST', API + '/' + observed['id'] + '/dispatch')
            print(json.dumps({k: run.get(k) for k in ['id', 'status', 'created_at']}))
        else:
            print(json.dumps({'automation_id': observed['id'], 'state': observed['state'], 'runs': [{k:r.get(k) for k in ['id', 'status', 'phase', 'conversation_id', 'sandbox_id', 'error_detail', 'started_at', 'completed_at']} for r in runs]}))


if __name__ == '__main__':
    try:
        main()
    except (DeploymentError, ValueError) as error:
        print(json.dumps({'error': str(error)}))
        raise SystemExit(1)
