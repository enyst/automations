import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import deploy_dead_code as deploy
from deploy_jev import API, OWNER, DeploymentError


class Cloud:
    credential = 'synthetic-credential'

    def __init__(self, duplicate=False, active=False, profile=True):
        self.writes = []
        self.duplicate, self.active, self.profile = duplicate, active, profile

    def request(self, method, path, body=None):
        if method == 'GET':
            if path == '/api/v1/users/me':
                return {'git_user_name': 'enyst', 'id': OWNER, 'org_id': OWNER}
            if path == API + '?limit=100':
                return {'total': 1 if self.duplicate else 0, 'automations': [{'name': deploy.NAME}] if self.duplicate else []}
            if path == '/api/v1/settings/profiles':
                return {'profiles': [{'name': 'deep-pro'}] if self.profile else []}
            if path == '/api/v1/secrets/search?limit=100':
                return {'items': [{'name': 'GITHUB_TOKEN'}]}
            if path.startswith(API + '/'):
                return {**deploy.request_body(), 'id': '11111111-1111-4111-8111-111111111111',
                        'user_id': OWNER, 'org_id': OWNER, 'entrypoint': '.venv/bin/python main.py',
                        'tarball_path': 'oh-internal://bundle', 'updated_at': 'fixture',
                        'state': 'ACTIVE' if self.active else 'INACTIVE', 'enabled': self.active}
        self.writes.append((method, path, body))
        if path.endswith('/preset/prompt'):
            return {'id': '11111111-1111-4111-8111-111111111111'}
        if method == 'PATCH':
            return {}
        raise AssertionError((method, path))


class Download:
    def bundle(self, identifier):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            for name, content in {'main.py': b'print("fixture")\n', 'setup.sh': b'echo setup\n'}.items():
                info = tarfile.TarInfo(name); info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        return output.getvalue()


class DeadCodeTests(unittest.TestCase):
    def test_inactive_at_creation_and_exact_cloud_bundle_retained(self):
        cloud = Cloud()
        with tempfile.TemporaryDirectory() as directory, patch.object(deploy, 'ROOT', Path(directory).resolve()):
            result = deploy.create(cloud, Download())
            self.assertEqual(result['dispatches'], 0)
            self.assertEqual(len(cloud.writes), 1)
            self.assertFalse(cloud.writes[0][2]['enabled'])
            self.assertEqual(cloud.writes[0][2]['state'], 'INACTIVE')
            bundle = Path(directory) / 'cloud-automations' / ('automation-' + result['id']) / 'tarball'
            self.assertEqual((bundle / 'main.py').read_bytes(), b'print("fixture")\n')

    def test_duplicate_and_missing_model_are_read_only(self):
        for cloud in (Cloud(duplicate=True), Cloud(profile=False)):
            with self.assertRaises(DeploymentError):
                deploy.create(cloud, Download())
            self.assertEqual(cloud.writes, [])

    def test_bad_active_readback_is_paused_and_not_exported(self):
        cloud = Cloud(active=True)
        with self.assertRaisesRegex(DeploymentError, 'creation_state_mismatch_paused'):
            deploy.create(cloud, Download())
        self.assertEqual(cloud.writes[-1][0], 'PATCH')
        self.assertFalse(cloud.writes[-1][2]['enabled'])

    def test_enabled_definition_rejected_before_network(self):
        value = json.loads((deploy.DEFINITION / 'request.json').read_text())
        value['enabled'] = True
        original = Path.read_text
        def read(path, *args, **kwargs):
            return json.dumps(value) if path.name == 'request.json' else original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read), self.assertRaises(DeploymentError):
            deploy.request_body()

    def test_prompt_keeps_reference_proof_and_full_final_gates(self):
        prompt = deploy.request_body()['prompt']
        for required in ('--production', '--noUnusedLocals', 'LanguageService.findReferences',
                         'FINAL candidate', 'byte-for-byte', 'Full test suite',
                         'ONE draft PR', 'NO_CHANGE', 'BLOCKED', '1800-second',
                         'Do not merge', 'Check again immediately before publishing'):
            self.assertIn(required, prompt)
