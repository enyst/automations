#!/usr/bin/env python3
"""Create a paused prompt preset and retain its exact Cloud bundle. Never dispatch."""
import argparse
import hashlib
import json
from pathlib import Path

from deploy_jev import API, CLOUD, OWNER, Client, DeploymentError, identity, keychain, secret_names
from export_cloud import ExportError, encoded, public_definition, scan, unpack, write_atomic

ROOT = Path(__file__).resolve().parents[1]
DEFINITION = ROOT / 'definitions/canvas-dead-code'
NAME = 'Canvas bounded dead-code'
PACKAGE = ROOT / 'cloud-automations/automation-e7aac5a4-a5b5-493e-a655-1b4ae02403a6'


def request_body():
    body = json.loads((DEFINITION / 'request.json').read_text())
    body['prompt'] = (PACKAGE / 'tarball/prompt.txt').read_text()
    if (body.get('name') != NAME or body.get('state') != 'INACTIVE'
            or body.get('enabled') is not False or body.get('timeout') != 1800
            or body.get('keep_alive') is not False
            or body.get('model') != 'deep-pro'
            or body.get('trigger') != {'type': 'cron', 'schedule': '0 0 1 1 *', 'timezone': 'UTC'}
            or body.get('repos') != [{'url': 'https://github.com/OpenHands/OpenHands', 'provider': 'github', 'ref': 'main'}]):
        raise DeploymentError('unexpected_dead_code_definition')
    if not body['prompt'].strip() or len(body['prompt']) > 50000:
        raise DeploymentError('invalid_prompt')
    scan(encoded(body))
    return body


def create(cloud, downloader):
    body = request_body()
    identity(cloud)
    listing = cloud.request('GET', API + '?limit=100')
    if listing.get('total', 0) > 100 or any(a.get('name') == NAME for a in listing.get('automations', [])):
        raise DeploymentError('inspect_inventory_before_create')
    profiles = cloud.request('GET', '/api/v1/settings/profiles')
    if not any(p.get('name') == body['model'] for p in profiles.get('profiles', [])):
        raise DeploymentError('model_profile_missing')
    if not secret_names(cloud).intersection({'GITHUB_TOKEN', 'github_token', 'REMOTE_GH'}):
        raise DeploymentError('github_secret_reference_missing')
    created = cloud.request('POST', API + '/preset/prompt', body)
    identifier = created['id']
    observed = cloud.request('GET', API + '/' + identifier)
    # Creation must be INACTIVE atomically; there is no create-then-disable window.
    if observed.get('state') != 'INACTIVE' or observed.get('enabled') is not False:
        cloud.request('PATCH', API + '/' + identifier, {'state': 'INACTIVE', 'enabled': False})
        raise DeploymentError('creation_state_mismatch_paused')
    expected = {'id': OWNER, 'org_id': OWNER}
    fields, binding = public_definition(observed, {'user_id': expected['id'], 'org_id': expected['org_id']})
    for key in ('name', 'prompt', 'model', 'timeout', 'keep_alive'):
        if fields.get(key) != body[key]:
            raise DeploymentError('creation_readback_mismatch')
    for key, value in body['trigger'].items():
        if fields['trigger'].get(key) != value:
            raise DeploymentError('creation_trigger_mismatch')
    archive = downloader.bundle(identifier)
    files, executables = unpack(archive, cloud.credential)
    if not {'main.py', 'setup.sh'}.issubset(files):
        raise DeploymentError('preset_bundle_incomplete')
    for name, data in files.items():
        if name.endswith('.py'):
            compile(data, name, 'exec')
    final = cloud.request('GET', API + '/' + identifier)
    if any(final.get(k) != observed.get(k) for k in ('state', 'enabled', 'prompt', 'model', 'tarball_path', 'updated_at')):
        raise DeploymentError('definition_changed_during_download')
    dest = ROOT / 'cloud-automations' / ('automation-' + identifier)
    if dest.exists():
        raise DeploymentError('export_destination_exists')
    if executables:
        fields['tarball_executables'] = executables
    metadata = encoded(fields)
    scan(metadata, cloud.credential)
    write_atomic(dest / 'automation.yaml', metadata)
    for name, data in files.items():
        target = dest / 'tarball' / name
        write_atomic(target, data)
        target.chmod(0o755 if name in executables else 0o644)
    receipt = {**binding, 'status': 'complete', 'state': 'INACTIVE', 'enabled': False,
               'bundle_sha256': hashlib.sha256(archive).hexdigest(),
               'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
               'dispatches': 0, 'provenance': 'cloud_prompt_preset_download'}
    write_atomic(dest / 'deployment.json', encoded(receipt))
    return {'id': identifier, 'state': 'INACTIVE', 'dispatches': 0, 'files': sorted(files)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['dry-run', 'create'])
    args = parser.parse_args(argv)
    if args.command == 'dry-run':
        print(json.dumps({'request': request_body(), 'network_requests': 0}))
        return 0
    from export_cloud import Client as DownloadClient
    credential = keychain('OPENHANDS_API_KEY')
    result = create(Client(CLOUD, credential), DownloadClient(credential))
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (DeploymentError, ExportError) as error:
        print(json.dumps({'error': str(error)}))
        raise SystemExit(1)
