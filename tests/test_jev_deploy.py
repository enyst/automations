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
        if method == "POST" and path == "/api/v1/secrets":
            self.writes.append(body)
            self.secrets[body["name"]] = body["value"]
            return {}
        raise AssertionError((method, path))


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
