import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
BUNDLE = ROOT / 'local-automations/cloud-sidebar-verification/tarball'
spec = importlib.util.spec_from_file_location('local_cloud_launcher', BUNDLE / 'main.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class Cloud:
    def __init__(self, kind='openhands', fail_send=False):
        self.kind = kind
        self.fail_send = fail_send
        self.calls = []
        self.cid = None

    def request(self, method, url, key, body=None, **kwargs):
        self.calls.append((method, url, body))
        if url.endswith('/users/me'):
            return {'id': '18881862-24a9-4521-ba8a-8424314c6458', 'org_id': '18881862-24a9-4521-ba8a-8424314c6458'}
        if method == 'POST' and url.endswith('/app-conversations'):
            self.cid = body['conversation_id']
            return {'id': 'task', 'status': 'PENDING'}
        if '/start-tasks?' in url:
            return [{'status': 'READY', 'app_conversation_id': self.cid, 'sandbox_id': 'cloud-sandbox'}]
        if '/app-conversations?ids=' in url:
            return [{'agent_kind': self.kind, 'llm_model': 'openhands/deepseek-v4-pro', 'execution_status': 'idle'}]
        if url.endswith('/send-message'):
            if self.fail_send:
                raise launcher.LauncherError('http_502')
            return {'success': True}
        raise AssertionError(url)


class LauncherTests(unittest.TestCase):
    def execute(self, cloud, directory, status=False):
        original = Path.read_text
        def read(path, *args, **kwargs):
            text = original(path, *args, **kwargs)
            if path == BUNDLE / 'config.json':
                config = json.loads(text); config['state_directory'] = directory
                return json.dumps(config)
            return text
        with patch.dict(os.environ, {'AGENT_SERVER_URL': 'http://localhost:18000'}, clear=True), \
                patch.object(Path, 'read_text', read), patch.object(launcher, 'get_secret', return_value='synthetic') as secret, \
                patch.object(launcher, 'request', side_effect=cloud.request):
            value = launcher.run(status)
        self.assertEqual(secret.call_args.args, ('OPENHANDS_API_KEY',))
        return value

    def test_local_submit_only_cloud_repo_config_no_local_checkout(self):
        cloud = Cloud()
        with tempfile.TemporaryDirectory() as directory:
            value = self.execute(cloud, directory)
            self.assertEqual(value['phase'], 'submitted')
            self.assertEqual(value['sandbox_id'], 'cloud-sandbox')
            body = next(body for method, url, body in cloud.calls if method == 'POST' and url.endswith('/app-conversations'))
            self.assertEqual(body['selected_repository'], 'OpenHands/OpenHands')
            self.assertEqual(body['selected_branch'], 'main')
            self.assertNotIn('secrets', body)
            self.assertFalse(body['initial_message']['run'])
            self.assertEqual(len([x for x in cloud.calls if x[1].endswith('/send-message')]), 1)

    def test_repeated_runs_monitor_same_worker_without_new_start(self):
        cloud = Cloud()
        with tempfile.TemporaryDirectory() as directory:
            first = self.execute(cloud, directory)
            second = self.execute(cloud, directory)
        self.assertEqual(first['conversation_id'], second['conversation_id'])
        self.assertEqual(len([x for x in cloud.calls if x[0] == 'POST' and x[1].endswith('/app-conversations')]), 1)
        self.assertEqual(len([x for x in cloud.calls if x[1].endswith('/send-message')]), 1)

    def test_ambiguous_send_is_not_automatically_repeated(self):
        cloud = Cloud(fail_send=True)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(launcher.LauncherError, 'http_502'):
                self.execute(cloud, directory)
            cloud.fail_send = False
            with self.assertRaisesRegex(launcher.LauncherError, 'send_uncertain'):
                self.execute(cloud, directory)
        self.assertEqual(len([x for x in cloud.calls if x[1].endswith('/send-message')]), 1)

    def test_acp_worker_rejected_before_run(self):
        cloud = Cloud(kind='acp')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(launcher.LauncherError, 'normal_openhands'):
                self.execute(cloud, directory)
        self.assertFalse(any(x[1].endswith('/send-message') for x in cloud.calls))

    def test_cloud_launch_environment_rejected_before_secret_access(self):
        with patch.dict(os.environ, {'SANDBOX_ID': 'remote'}, clear=True), patch.object(launcher, 'get_secret') as secret:
            with self.assertRaisesRegex(launcher.LauncherError, 'local_launcher_required'):
                launcher.run()
            secret.assert_not_called()

    def test_repo_metadata_and_prompt_no_clone_instruction(self):
        repos = json.loads((BUNDLE / 'repos_config.json').read_text())
        prompt = (BUNDLE / 'prompt.txt').read_text()
        self.assertEqual(repos[0]['url'], 'https://github.com/OpenHands/OpenHands')
        self.assertNotIn('clone', prompt.lower())
        self.assertIn('F02.sidebar-collapse', prompt)
        source = (BUNDLE / 'main.py').read_text()
        self.assertNotIn('import subprocess', source)
        self.assertNotIn('clone_repos(', source)

    def test_status_with_no_state_never_creates_worker(self):
        cloud = Cloud()
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.execute(cloud, directory, status=True), {'phase': 'not_submitted'})
        self.assertFalse(any(method == 'POST' for method, _, _ in cloud.calls))

    def test_ambiguous_create_never_automatically_creates_again(self):
        cloud = Cloud()
        original = cloud.request
        def failing(method, url, key, body=None, **kwargs):
            value = original(method, url, key, body, **kwargs)
            if method == 'POST' and url.endswith('/app-conversations'):
                raise launcher.LauncherError('network_unavailable')
            return value
        cloud.request = failing
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(launcher.LauncherError, 'network_unavailable'):
                self.execute(cloud, directory)
            with self.assertRaisesRegex(launcher.LauncherError, 'creation_uncertain'):
                self.execute(cloud, directory)
        self.assertEqual(len([x for x in cloud.calls if x[0] == 'POST' and x[1].endswith('/app-conversations')]), 1)

    def test_main_fires_success_and_failure_callbacks(self):
        for value, error, expected in [({'phase': 'submitted'}, None, 'COMPLETED'), (None, launcher.LauncherError('synthetic_failure'), 'FAILED')]:
            with patch('sys.argv', ['main.py']), patch.object(launcher, 'run', return_value=value, side_effect=error), patch.object(launcher, 'fire_callback') as callback:
                code = launcher.main()
            self.assertEqual(code, 0 if error is None else 1)
            self.assertEqual(callback.call_args.args[0] if callback.call_args.args else 'COMPLETED', expected)

    def test_cloud_already_running_initial_message_is_not_sent_twice(self):
        cloud = Cloud()
        original = cloud.request
        def started(method, url, key, body=None, **kwargs):
            value = original(method, url, key, body, **kwargs)
            if '/app-conversations?ids=' in url:
                value[0]['execution_status'] = 'running'
            return value
        cloud.request = started
        with tempfile.TemporaryDirectory() as directory:
            value = self.execute(cloud, directory)
        self.assertTrue(value['sent'])
        self.assertTrue(value['started_from_initial_message'])
        self.assertFalse(any(url.endswith('/send-message') for _, url, _ in cloud.calls))
