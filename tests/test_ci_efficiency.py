import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import deploy_ci_efficiency as deploy


class TrialTests(unittest.TestCase):
    def test_definition_stays_inactive_and_bounded(self):
        d = deploy.definition()
        self.assertFalse(d['enabled'])
        self.assertFalse(d['keep_alive'])
        self.assertEqual(d['timeout'], 1500)

    def test_no_gate_or_profile_fallback_or_bulk_secret_forwarding(self):
        source = (deploy.PACKAGE / 'tarball/main.py').read_text()
        self.assertNotIn('import gate', source)
        self.assertNotIn('except FileNotFoundError', source)
        self.assertNotIn('workspace.get_secrets()', source)
        self.assertIn('mcp_config = {}', source)

    def test_bundle_compiles_and_is_deterministic(self):
        a, files = deploy.bundle(deploy.PACKAGE / 'tarball')
        self.assertEqual(a, deploy.bundle(deploy.PACKAGE / 'tarball')[0])
        self.assertEqual(len(files), 6)

    def test_readonly_prompt_has_caps_and_no_telemetry_claims(self):
        prompt = (deploy.PACKAGE / 'tarball/prompt.txt').read_text()
        for expected in ['REPORT ONLY', '500 workflow runs', '20 run/job', 'DISABLED', 'Do not infer dead code', 'INCONCLUSIVE', 'finish tool']:
            self.assertIn(expected, prompt)

    def test_identity_refuses_wrong_account(self):
        spec = importlib.util.spec_from_file_location('trial_identity', deploy.PACKAGE / 'tarball/github_identity.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        class Secret:
            def get_value(self): return 'synthetic'
        class Workspace:
            def get_secrets(self, names):
                self.names = names
                return {'SMOLPAWS_TOKEN': Secret()}
        workspace = Workspace()
        with patch.object(module.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"login":"enyst"}'
            with self.assertRaisesRegex(RuntimeError, 'not smolpaws'):
                module.github_secrets(workspace)
        self.assertEqual(workspace.names, ['SMOLPAWS_TOKEN'])

    def test_owned_rejects_active_definition(self):
        class Cloud:
            def request(self, *args):
                return {'name': deploy.NAME, 'user_id':deploy.OWNER, 'org_id':deploy.OWNER, 'state':'ACTIVE', 'enabled':True}
        with self.assertRaisesRegex(deploy.DeploymentError, 'wrong_or_active'):
            deploy.owned(Cloud(), '11111111-1111-4111-8111-111111111111')

    def test_verify_rejects_changed_bundle(self):
        observed = {**deploy.definition(), 'id': 'trial', 'user_id': deploy.OWNER, 'org_id': deploy.OWNER}
        with patch.object(deploy, 'owned', return_value=observed), patch.object(deploy, 'ExportClient') as client:
            client.return_value.get.return_value = b'changed'
            cloud = type('Cloud', (), {'credential': 'synthetic'})()
            with self.assertRaisesRegex(deploy.DeploymentError, 'bundle_readback_mismatch'):
                deploy.verify(cloud, 'trial', b'expected')

    def test_verify_rejects_changed_setting(self):
        observed = {**deploy.definition(), 'timeout': 999}
        with patch.object(deploy, 'owned', return_value=observed), patch.object(deploy, 'ExportClient') as client:
            with self.assertRaisesRegex(deploy.DeploymentError, 'readback_mismatch:timeout'):
                deploy.verify(object(), 'trial', b'expected')
            client.assert_not_called()

    def test_dispatch_is_explicit_and_refuses_overlap(self):
        class Cloud:
            credential = 'synthetic'
            def __init__(self): self.writes = []
            def request(self, method, path, body=None):
                if method == 'GET': return {'runs': [{'status': 'RUNNING'}]}
                self.writes.append(path)
                raise AssertionError('unexpected write')
        cloud = Cloud()
        with patch.object(sys, 'argv', ['deploy', 'dispatch', '--automation-id', 'trial']), patch.object(deploy, 'Client', return_value=cloud), patch.object(deploy, 'keychain', return_value='synthetic'), patch.object(deploy, 'identity'), patch.object(deploy, 'verify', return_value={'id':'trial'}):
            with self.assertRaisesRegex(deploy.DeploymentError, 'trial_already_running'):
                deploy.main()
        self.assertEqual(cloud.writes, [])
