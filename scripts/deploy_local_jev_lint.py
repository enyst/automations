#!/usr/bin/env python3
"""Install a paused local Jev experiment. Never dispatch implicitly."""
import argparse
import hashlib
import json
from pathlib import Path

from deploy_jev import DeploymentError, bundle
from deploy_local_cloud_verification import LocalClient, API

PACKAGE = Path(__file__).resolve().parents[1] / 'local-automations/jev-lint'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['dry-run', 'install'])
    args = parser.parse_args()
    desired = json.loads((PACKAGE / 'automation.yaml').read_text())
    if desired.get('enabled') is not False or desired.get('state') != 'INACTIVE' or desired.get('entrypoint') != 'python3 focused.py':
        raise DeploymentError('paused_definition_required')
    archive, files = bundle(PACKAGE / 'tarball')
    receipt = {'bundle_sha256': hashlib.sha256(archive).hexdigest(), 'files': files,
               'backend': 'local', 'dispatches': 0}
    if args.command == 'dry-run':
        print(json.dumps(receipt)); return
    local = LocalClient()
    services = local.request('GET', '/server_info').get('runtime_services', {}).get('services', {})
    if services.get('automation', {}).get('url_from_agent') != 'http://localhost:18001':
        raise DeploymentError('unexpected_local_backend')
    listing = local.request('GET', API + '?limit=100')
    if listing.get('total', 0) > 100 or any(x['name'] == desired['name'] for x in listing.get('automations', [])):
        raise DeploymentError('inspect_existing_definition')
    archive, files = bundle(PACKAGE / 'tarball', (local.key,))
    upload = local.request('POST', API + '/uploads?name=jev-lint', archive, 'application/gzip')
    if upload.get('status') != 'COMPLETED':
        raise DeploymentError('upload_incomplete')
    desired['tarball_path'] = upload['tarball_path']
    check = local.request('POST', API + '/validate', {'endpoint': '/v1', 'draft': desired})
    if check.get('valid') is not True:
        raise DeploymentError('invalid_definition')
    created = local.request('POST', API, desired)
    observed = local.request('GET', API + '/' + created['id'])
    if observed.get('enabled') is not False or observed.get('state') != 'INACTIVE':
        local.request('PATCH', API + '/' + created['id'], {'state': 'INACTIVE', 'enabled': False})
        raise DeploymentError('readback_not_paused')
    receipt.update(id=created['id'], state='INACTIVE')
    (PACKAGE / 'deployment.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    try:
        main()
    except DeploymentError as e:
        print(json.dumps({'error': str(e)})); raise SystemExit(1)
