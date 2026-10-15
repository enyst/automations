#!/usr/bin/env python3
"""Explicit Cloud Jev-lint update. No dispatch or enablement on import."""
import argparse
import hashlib
import json
from pathlib import Path
from deploy_jev import Client, CLOUD, API, bundle, identity, keychain

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = 'ad72eec2-5dad-44ff-9c23-92a78a9c003a'
PACKAGE = ROOT / 'cloud-automations' / ('automation-' + IDENTIFIER)
TRIGGER = {
    'type': 'event', 'source': 'github', 'on': 'pull_request.*',
    'filter': "contains(['opened', 'synchronize', 'ready_for_review'], action) && pull_request.user.login == 'enyst' && pull_request.draft == `false` && pull_request.state == 'open' && contains(['OpenHands/OpenHands', 'OpenHands/software-agent-sdk', 'OpenHands/automation'], repository.full_name)",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['dry-run', 'deploy'])
    args = parser.parse_args()
    archive, files = bundle(PACKAGE / 'tarball')
    if args.command == 'dry-run':
        print(json.dumps({'files': files, 'sha256': hashlib.sha256(archive).hexdigest()}))
        return
    cloud = Client(CLOUD, keychain('OPENHANDS_API_KEY'))
    identity(cloud)
    before = cloud.request('GET', API + '/' + IDENTIFIER)
    assert before['name'] == 'jev-lint'
    archive, files = bundle(PACKAGE / 'tarball', (cloud.credential,))
    upload = cloud.request('POST', API + '/uploads?name=jev-lint', archive, 'application/gzip')
    assert upload['status'] == 'COMPLETED'
    desired = dict(name='jev-lint', entrypoint='python3 pr_lint.py', trigger=TRIGGER,
                   timeout=600, keep_alive=False, tarball_path=upload['tarball_path'])
    valid = cloud.request('POST', API + '/validate', {'endpoint': '/v1', 'draft': desired})
    assert valid['valid'], valid
    cloud.request('PATCH', API + '/' + IDENTIFIER, desired)
    cloud.request('PATCH', API + '/' + IDENTIFIER, {'enabled': True, 'state': 'ACTIVE'})
    after = cloud.request('GET', API + '/' + IDENTIFIER)
    assert after['enabled'] is True and after['state'] == 'ACTIVE'
    assert all(after['trigger'].get(k) == v for k, v in TRIGGER.items())
    assert after['entrypoint'] == desired['entrypoint']
    definition = {k: after.get(k) for k in ['name', 'entrypoint', 'enabled', 'state', 'trigger', 'timeout', 'keep_alive', 'setup_script_path']}
    (PACKAGE / 'automation.yaml').write_text(json.dumps(definition, indent=2)+'\n')
    receipt = {'id': IDENTIFIER, **definition, 'bundle_sha256': hashlib.sha256(archive).hexdigest(), 'files': files, 'dispatches': 0}
    (PACKAGE / 'cloud-deployment.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
