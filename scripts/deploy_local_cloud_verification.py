#!/usr/bin/env python3
"""Explicit local-only paused install. No Cloud worker dispatch or verification."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

from deploy_jev import DeploymentError, bundle, NoRedirect

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'local-automations/cloud-sidebar-verification'
HOST = 'http://127.0.0.1:8000'
API = '/api/automation/v1'


class LocalClient:
    def __init__(self):
        self.key = (Path.home() / '.openhands/agent-canvas/api-key.txt').read_text().strip()
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method, path, body=None, content_type='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(HOST + path, data=data, method=method,
            headers={'X-Session-API-Key': self.key, 'Content-Type': content_type})
        try:
            with self.opener.open(req, timeout=45) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise DeploymentError('local_http_' + str(error.code)) from None


def definition():
    value = json.loads((PACKAGE / 'automation.yaml').read_text())
    if value.get('enabled') is not False or value.get('state') != 'INACTIVE' or value.get('entrypoint') != 'python3 main.py':
        raise DeploymentError('paused_local_definition_required')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['dry-run', 'install'])
    args = parser.parse_args()
    desired = definition()
    archive, files = bundle(PACKAGE / 'tarball')
    if args.command == 'dry-run':
        print(json.dumps({'definition': desired, 'files': files,
            'sha256': hashlib.sha256(archive).hexdigest(), 'network_requests': 0}))
        return
    local = LocalClient()
    info = local.request('GET', '/server_info')
    services = info.get('runtime_services', {}).get('services', {})
    if services.get('automation', {}).get('url_from_agent') != 'http://localhost:18001':
        raise DeploymentError('unexpected_local_automation_backend')
    listing = local.request('GET', API + '?limit=100')
    if listing.get('total', 0) > 100 or any(x['name'] == desired['name'] for x in listing.get('automations', [])):
        raise DeploymentError('inspect_existing_local_definition')
    archive, files = bundle(PACKAGE / 'tarball', (local.key,))
    upload = local.request('POST', API + '/uploads?name=local-cloud-sidebar-verification', archive, 'application/gzip')
    if upload.get('status') != 'COMPLETED':
        raise DeploymentError('local_upload_incomplete')
    desired['tarball_path'] = upload['tarball_path']
    check = local.request('POST', API + '/validate', {'endpoint': '/v1', 'draft': desired})
    if check.get('valid') is not True:
        raise DeploymentError('local_definition_invalid')
    created = local.request('POST', API, desired)
    observed = local.request('GET', API + '/' + created['id'])
    if observed['enabled'] is not False or observed['state'] != 'INACTIVE':
        local.request('PATCH', API + '/' + created['id'], {'state': 'INACTIVE', 'enabled': False})
        raise DeploymentError('local_state_mismatch_paused')
    if any(observed.get(k) != v for k, v in desired.items() if k != 'trigger'):
        raise DeploymentError('local_readback_mismatch')
    receipt = {'id': created['id'], 'backend': 'local', 'state': 'INACTIVE', 'dispatches': 0,
               'bundle_sha256': hashlib.sha256(archive).hexdigest(), 'files': files}
    (PACKAGE / 'deployment.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    try:
        main()
    except DeploymentError as error:
        print(json.dumps({'error': str(error)})); raise SystemExit(1)
