import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "jev_deploy", Path(__file__).parents[1] / "scripts/deploy_jev.py"
)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


class FakeCloud:
    def __init__(self, secrets):
        self.secrets = dict(secrets)
        self.writes = []

    def request(self, method, path, body=None):
        if method == "GET" and path == "/api/v1/users/me":
            return {"id": deploy.OWNER, "org_id": deploy.OWNER, "git_user_name": "enyst"}
        if method == "GET" and path == "/api/v1/secrets/search?limit=100":
            return {"items": [{"name": name} for name in self.secrets]}
        if method == "GET" and path == deploy.API + "/capabilities":
            return {"ready": True, "triggerKinds": ["cron"]}
        if method == "POST" and path == "/api/v1/secrets":
            self.writes.append(body)
            self.secrets[body["name"]] = body["value"]
            return {}
        raise AssertionError((method, path))


class FakeGitHub:
    def __init__(self, repo, login="enyst"):
        self.repo = repo
        self.login = login
        self.requests = []

    def request(self, method, path, body=None):
        self.requests.append((method, path))
        if method == "GET" and path == "/user":
            return {"login": self.login}
        if method == "GET" and path == "/repos/enyst/automations":
            return self.repo
        raise AssertionError((method, path))


class GitHubIdentityTests(unittest.TestCase):
    def verify(self, github):
        with patch.object(deploy, "Client", return_value=github), \
                patch.object(deploy, "keychain", return_value="synthetic-github-key"):
            return deploy.github_identity()

    def test_exact_repository_accepts_public_and_private(self):
        for private in (False, True):
            with self.subTest(private=private):
                github = FakeGitHub({"full_name": "enyst/automations", "private": private})
                self.assertIs(self.verify(github), github)

    def test_wrong_account_stops_before_repository_lookup(self):
        github = FakeGitHub({"full_name": "enyst/automations", "private": False}, login="another-user")
        with self.assertRaisesRegex(deploy.DeploymentError, "^wrong_github_identity$"):
            self.verify(github)
        self.assertEqual(github.requests, [("GET", "/user")])

    def test_wrong_repository_rejected_for_both_visibilities(self):
        for private in (False, True):
            with self.subTest(private=private):
                github = FakeGitHub({"full_name": "another-user/automations", "private": private})
                with self.assertRaisesRegex(deploy.DeploymentError, "^wrong_backup_repository$"):
                    self.verify(github)

    def test_missing_or_malformed_visibility_is_not_assumed_public(self):
        for repo in ({"full_name": "enyst/automations"}, *[
                {"full_name": "enyst/automations", "private": value}
                for value in (None, 0, 1, "false", "true")]):
            with self.subTest(repo=repo):
                with self.assertRaisesRegex(deploy.DeploymentError, "^unknown_backup_repository_visibility$"):
                    self.verify(FakeGitHub(repo))

    def test_preflight_reports_observed_visibility_without_writes(self):
        for private in (False, True):
            with self.subTest(private=private):
                github = FakeGitHub({"full_name": "enyst/automations", "private": private})
                cloud = FakeCloud({"TYPESAFE_API_KEY": "existing-classifier-key"})
                output = io.StringIO()
                def client(host, credential):
                    return github if host == "https://api.github.com" else cloud
                with patch.object(deploy, "Client", side_effect=client), \
                        patch.object(deploy, "keychain", return_value="synthetic-credential"), \
                        contextlib.redirect_stdout(output):
                    self.assertEqual(deploy.main(["preflight"]), 0)
                result = json.loads(output.getvalue())
                self.assertIs(result["private"], private)
                self.assertEqual(result["repo"], "enyst/automations")
                self.assertEqual(cloud.writes, [])
                self.assertTrue(all(method == "GET" for method, _ in github.requests))
                self.assertNotIn("synthetic-credential", output.getvalue())


class InstallSecretsTests(unittest.TestCase):
    def run_install(self, cloud):
        output = io.StringIO()
        values = {"OPENHANDS_API_KEY": "synthetic-cloud-key", "TYPESAFE_API_KEY": "synthetic-typesafe-key"}
        with patch.object(deploy, "Client", return_value=cloud), \
                patch.object(deploy, "keychain", side_effect=values.__getitem__) as keychain, \
                patch.object(deploy, "github_identity"), \
                contextlib.redirect_stdout(output):
            self.assertEqual(deploy.main(["install-secrets"]), 0)
        return json.loads(output.getvalue()), keychain.call_args_list

    def test_missing_classifier_key_is_installed_without_copying_github_credentials(self):
        cloud = FakeCloud({"REMOTE_GH": "existing-remote-credential"})
        result, reads = self.run_install(cloud)
        self.assertEqual([item["name"] for item in cloud.writes], ["TYPESAFE_API_KEY"])
        self.assertEqual(cloud.secrets["TYPESAFE_API_KEY"], "synthetic-typesafe-key")
        self.assertEqual(cloud.secrets["REMOTE_GH"], "existing-remote-credential")
        self.assertNotIn("ENYST_GH_TOKEN", cloud.secrets)
        self.assertEqual(result["installed_secret_names"], ["TYPESAFE_API_KEY"])
        self.assertEqual([call.args[0] for call in reads], ["OPENHANDS_API_KEY", "TYPESAFE_API_KEY"])
        self.assertNotIn("synthetic-typesafe-key", json.dumps(result))

    def test_existing_classifier_and_github_secrets_are_preserved(self):
        original = {"TYPESAFE_API_KEY": "existing-classifier-key", "ENYST_GH_TOKEN": "existing-added-token", "REMOTE_GH": "existing-remote-token"}
        cloud = FakeCloud(original)
        result, reads = self.run_install(cloud)
        self.assertEqual(cloud.secrets, original)
        self.assertEqual(cloud.writes, [])
        self.assertEqual(result["installed_secret_names"], [])
        self.assertEqual(result["existing_preserved"], ["TYPESAFE_API_KEY"])
        self.assertEqual([call.args[0] for call in reads], ["OPENHANDS_API_KEY"])


if __name__ == "__main__":
    unittest.main()
