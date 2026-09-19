"""Deployment boundaries with synthetic files and a mocked Cloud API only."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("field_notes_deploy", SCRIPTS / "deploy_field_notes.py")
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)

IDENTIFIER = "11111111-2222-4333-8444-555555555555"
RUNTIME = {"main.py", "core.py", "writer.py", "transport.py", "config.json", "setup.sh", "RUBRIC.md"}


def runtime_files(root):
    for name in RUNTIME:
        (root / name).write_text("{}\n" if name.endswith(".json") else "# Synthetic runtime fixture\n")


class RuntimeBundle(unittest.TestCase):
    def test_only_runtime_allowlist_is_exported_even_with_unrelated_secret_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            runtime_files(source)
            (source / "test_core.py").write_text("raise AssertionError('must not run or ship')\n")
            (source / ".env").write_text("NEVER_EXPORT=synthetic-private-value\n")
            (source / "notes-ref.md").write_text("Private working references\n")
            payload, names = d.runtime_bundle(source, known_secrets=("synthetic-private-value",))
        self.assertEqual(set(names), RUNTIME)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            self.assertEqual(set(archive.getnames()), RUNTIME)
            self.assertTrue(all(member.isfile() and not member.issym() for member in archive.getmembers()))
            self.assertNotIn(b"synthetic-private-value", b"".join(archive.extractfile(name).read() for name in archive.getnames()))

    def test_runtime_symlinks_and_missing_files_are_rejected(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary)
                runtime_files(source)
                (source / "writer.py").unlink()
                if symlink:
                    (source / "writer.py").symlink_to(source / "core.py")
                with self.assertRaisesRegex(d.DeploymentError, "runtime_file_missing_or_symlink"):
                    d.runtime_bundle(source)

    def test_known_and_recognizable_credentials_in_runtime_are_rejected(self):
        for content, known in [("synthetic-live-credential", ("synthetic-live-credential",)),
                               ("ghp_" + "A" * 36, ())]:
            with self.subTest(known=bool(known)), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary)
                runtime_files(source)
                (source / "main.py").write_text("credential = " + repr(content) + "\n")
                with self.assertRaisesRegex(d.DeploymentError, "credential_in_bundle"):
                    d.runtime_bundle(source, known_secrets=known)


class Cloud:
    credential = "synthetic-cloud-credential"

    def __init__(self, *, login="enyst", inventory=None, identity_change=None, enabled=False, trigger=None):
        self.login = login
        self.inventory = inventory if inventory is not None else {"total": 0, "automations": []}
        self.row = {"id": IDENTIFIER, "name": d.NAME, "user_id": d.OWNER, "org_id": d.OWNER,
                    "enabled": enabled, "trigger": copy.deepcopy(trigger or d.STAGING),
                    "tarball_path": "old-bundle"}
        self.row.update(identity_change or {})
        self.writes = []

    def request(self, method, path, body=None, content_type=None):
        if method != "GET": self.writes.append((method, path, copy.deepcopy(body)))
        if path == "/api/v1/users/me":
            return {"git_user_name": self.login, "id": d.OWNER, "org_id": d.OWNER}
        if path == "/api/v1/secrets/search?limit=100":
            return {"items": [{"name": "TYPESAFE_API_KEY"}]}
        if method == "GET" and path == d.API + "?limit=100": return copy.deepcopy(self.inventory)
        if method == "GET" and path == d.API + "/" + IDENTIFIER: return copy.deepcopy(self.row)
        if method == "POST" and path.startswith(d.API + "/uploads?"):
            return {"status": "COMPLETED", "tarball_path": "new-bundle"}
        if method == "POST" and path == d.API + "/validate": return {"valid": True}
        if method == "PATCH" and path == d.API + "/" + IDENTIFIER:
            self.row.update(copy.deepcopy(body))
            return copy.deepcopy(self.row)
        if method == "POST" and path == d.API:
            self.row.update(copy.deepcopy(body))
            self.row["enabled"] = True
            return copy.deepcopy(self.row)
        if method == "POST" and path == d.API + "/" + IDENTIFIER + "/dispatch":
            return {"id": "synthetic-run", "status": "PENDING"}
        raise AssertionError((method, path))


class Deployment(unittest.TestCase):
    def invoke(self, cloud, *arguments):
        with patch.object(d, "Client", return_value=cloud), patch.object(d, "keychain", return_value=cloud.credential), \
             patch.object(d, "runtime_bundle", return_value=(b"synthetic-archive", sorted(RUNTIME))), \
             patch.object(sys, "argv", ["deploy_field_notes.py", *arguments]), patch("sys.stdout", new=io.StringIO()) as output:
            d.main()
        self.assertNotIn(cloud.credential, output.getvalue())
        return json.loads(output.getvalue())

    def test_wrong_account_prevents_all_mutations(self):
        cloud = Cloud(login="different-user")
        with self.assertRaisesRegex(d.DeploymentError, "wrong_cloud_identity"):
            self.invoke(cloud, "stage")
        self.assertEqual(cloud.writes, [])

    def test_wrong_automation_name_owner_or_org_prevents_mutation(self):
        for change in [{"name": "Different automation"}, {"user_id": "other-user"}, {"org_id": "other-org"}]:
            with self.subTest(change=change):
                cloud = Cloud(identity_change=change)
                with self.assertRaisesRegex(d.DeploymentError, "automation_identity_mismatch"):
                    self.invoke(cloud, "stage", "--automation-id", IDENTIFIER)
                self.assertEqual(cloud.writes, [])

    def test_create_rejects_existing_name_and_incomplete_inventory(self):
        for inventory, error in [({"total": 1, "automations": [{"name": d.NAME}]}, "use_existing_automation_id"),
                                 ({"total": 101, "automations": []}, "inventory_pagination_required")]:
            with self.subTest(error=error):
                cloud = Cloud(inventory=inventory)
                with self.assertRaisesRegex(d.DeploymentError, error): self.invoke(cloud, "stage")
                self.assertEqual(cloud.writes, [])

    def test_staging_new_and_existing_uses_annual_schedule_and_finishes_paused(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                cloud = Cloud()
                arguments = ["stage", "--classify-only"] + (["--automation-id", IDENTIFIER] if existing else [])
                result = self.invoke(cloud, *arguments)
                self.assertFalse(result["automation"]["enabled"])
                self.assertEqual(cloud.row["trigger"], d.STAGING)
                self.assertEqual(cloud.row["entrypoint"], ".venv/bin/python main.py")
                self.assertEqual(cloud.row["tarball_path"], "new-bundle")
                creates = [write for write in cloud.writes if write[:2] == ("POST", d.API)]
                self.assertEqual(len(creates), 0 if existing else 1)

    def test_paused_manual_dispatch_rejects_hourly_schedule_without_mutations(self):
        cloud = Cloud(trigger={"type": "cron", "schedule": "15 * * * *", "timezone": "UTC"})
        with self.assertRaisesRegex(d.DeploymentError, "manual_trial_requires_staging_schedule"):
            self.invoke(cloud, "dispatch", "--automation-id", IDENTIFIER)
        self.assertEqual(cloud.writes, [])

    def test_paused_annual_manual_dispatch_enables_existing_definition_once(self):
        cloud = Cloud()
        result = self.invoke(cloud, "dispatch", "--automation-id", IDENTIFIER)
        self.assertEqual(result["run"]["status"], "PENDING")
        self.assertEqual(cloud.writes, [
            ("PATCH", d.API + "/" + IDENTIFIER, {"enabled": True}),
            ("POST", d.API + "/" + IDENTIFIER + "/dispatch", None),
        ])
        self.assertEqual(cloud.row["trigger"], d.STAGING)


if __name__ == "__main__": unittest.main()
