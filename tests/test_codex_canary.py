import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "cloud-automations/automation-c26d603f-aac4-4283-a605-85b58d998758/tarball"
SPEC = importlib.util.spec_from_file_location("codex_canary", SOURCE / "main.py")
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)


class CanaryTests(unittest.TestCase):
    def test_conversation_uses_saved_profile_without_sending_secrets(self):
        config = json.loads((SOURCE / "config.json").read_text())
        body = canary.start_body(config, "11111111-1111-4111-8111-111111111111", "owned-sandbox", "bounded task")
        self.assertNotIn("secrets", body)
        self.assertEqual(body["agent_profile_id"], config["agent_profile_id"])
        self.assertEqual(body["sandbox_id"], "owned-sandbox")
        self.assertEqual(body["selected_repository"], "OpenHands/OpenHands")
        self.assertFalse(body["public"])
        self.assertFalse(body["initial_message"]["run"])
        self.assertEqual(body, canary.start_body(config, "11111111-1111-4111-8111-111111111111", "owned-sandbox", "bounded task"))

    def test_callback_refuses_foreign_origin(self):
        env = {"AUTOMATION_CALLBACK_URL": "https://foreign.example/callback", "AUTOMATION_CALLBACK_API_KEY": "synthetic", "OPENHANDS_CLOUD_API_URL": "https://app.all-hands.dev"}
        with patch.dict(canary.os.environ, env), patch.object(canary, "request") as request:
            with self.assertRaisesRegex(canary.CanaryError, "callback_origin_mismatch"):
                canary.callback("FAILED")
            request.assert_not_called()

    def test_callback_includes_run_and_conversation_identity(self):
        env = {"AUTOMATION_CALLBACK_URL": "https://app.all-hands.dev/api/automation/v1/runs/run/complete", "AUTOMATION_CALLBACK_API_KEY": "synthetic", "OPENHANDS_CLOUD_API_URL": "https://app.all-hands.dev", "AUTOMATION_RUN_ID": "run"}
        with patch.dict(canary.os.environ, env), patch.object(canary, "request") as request:
            canary.callback("COMPLETED", "conversation")
            self.assertEqual(request.call_args.args[3], {"status": "COMPLETED", "run_id": "run", "conversation_id": "conversation"})

    def test_no_environment_or_identity_is_not_a_model_run(self):
        with patch.dict(canary.os.environ, {}, clear=True), patch.object(canary, "request") as request:
            with self.assertRaisesRegex(canary.CanaryError, "cloud_context_missing_or_mismatched"):
                canary.run()
            request.assert_not_called()

    def test_definition_is_inactive_and_provider_secrets_are_references(self):
        definition = json.loads((SOURCE.parent / "automation.yaml").read_text())
        profile = json.loads((SOURCE.parent / "agent-profile.json").read_text())
        self.assertEqual(definition["state"], "INACTIVE")
        self.assertEqual(profile["acp_server"], "codex")
        self.assertIn("CODEX_AUTH_JSON", profile["secret_refs"])
        self.assertNotIn("OPENAI_API_KEY", profile["secret_refs"])

    def test_unrestricted_or_wrong_profile_is_blocked_before_run(self):
        for profile in (None, {"agent_profile_id": "profile", "secret_refs": None},
                        {"agent_profile_id": "profile", "secret_refs": ["CODEX_AUTH_JSON", "UNRELATED_KEY"]}):
            with self.subTest(profile=profile):
                with self.assertRaises(canary.CanaryError):
                    canary.assert_profile_scope({"launched_agent_profile": profile}, "profile")

    def test_exact_secret_scope_is_accepted(self):
        canary.assert_profile_scope({"launched_agent_profile": {
            "agent_profile_id": "profile", "secret_refs": ["GITHUB_TOKEN", "CODEX_AUTH_JSON", "DEEPSEEK_API_KEY"]}}, "profile")

    def test_callback_accepts_account_key_when_cloud_omits_callback_key(self):
        env = {"AUTOMATION_CALLBACK_URL": "https://app.all-hands.dev/api/automation/v1/runs/run/complete", "OPENHANDS_API_KEY": "synthetic-account", "OPENHANDS_CLOUD_API_URL": "https://app.all-hands.dev", "AUTOMATION_RUN_ID": "run"}
        with patch.dict(canary.os.environ, env, clear=True), patch.object(canary, "request") as request:
            canary.callback("COMPLETED", "conversation")
            self.assertEqual(request.call_args.args[2], "synthetic-account")

    def test_run_message_repeats_complete_task_for_idle_acp_start(self):
        config = json.loads((SOURCE / "config.json").read_text())
        run_id = "11111111-1111-4111-8111-111111111111"
        cid = canary.start_body(config, run_id, "sandbox", "task")["conversation_id"]
        messages = []
        def request(method, url, key, body=None):
            if url.endswith("/users/me"):
                return {"id": config["owner_id"], "org_id": config["owner_id"]}
            if method == "POST" and url.endswith("/app-conversations"):
                return {"id": "task", "status": "READY", "app_conversation_id": cid, "sandbox_id": "sandbox"}
            if method == "POST" and url.endswith("/send-message"):
                messages.append(body)
                return {}
            if "/file?" in url:
                return json.dumps({"verdict": "pass", "feature_id": config["feature_id"], "checkout_sha": config["target_sha"], "cleanup_verified": True})
            return [{"agent_kind": "acp", "acp_server": "codex", "execution_status": "finished" if messages else "idle"}]
        env = {"OPENHANDS_CLOUD_API_URL": config["cloud_url"], "OPENHANDS_API_KEY": "synthetic", "SANDBOX_ID": "sandbox", "AUTOMATION_RUN_ID": run_id}
        with patch.dict(canary.os.environ, env, clear=True), patch.object(canary, "request", side_effect=request), patch.object(canary.time, "sleep"), patch.object(canary, "callback"), patch.object(canary.Path, "write_text"):
            canary.run()
        self.assertEqual(messages[0]["content"][0]["text"], (SOURCE / "prompt.txt").read_text() + "\nPinned target SHA: " + config["target_sha"])
        self.assertTrue(messages[0]["run"])
