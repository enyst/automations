import copy
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import urllib.error

spec = importlib.util.spec_from_file_location(
    "export_cloud", Path(__file__).parents[1] / "scripts/export_cloud.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

ORG = "18881862-24a9-4521-ba8a-8424314c6458"
ID = "fe2c8185-2222-4333-8444-555555555555"
OTHER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def archive(members=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as handle:
        for name, content, mode, kind in members or [("main.py", b"print('hello')\n", 0o755, "file")]:
            member = tarfile.TarInfo(name)
            member.mode = mode
            if kind == "link":
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
            else:
                member.size = len(content)
            handle.addfile(member, io.BytesIO(content) if kind == "file" else None)
    return buffer.getvalue()


def definition(automation_id=ID):
    return {"id": automation_id, "user_id": ORG, "org_id": ORG,
            "name": "Attention", "trigger": {"type": "cron", "schedule": "0 9 * * 1"},
            "entrypoint": "python main.py", "enabled": False,
            "tarball_path": "oh-internal://uploads/current",
            "updated_at": "2026-09-18T00:00:00Z", "prompt": "Review recent changes.",
            "agent_profile_id": "astra-review-auditor",
            "unknown_secret_payload": "MUST-NOT-EXPORT"}


class FakeClient:
    credential = "runtime-credential-only"

    def __init__(self, rows=None, bundle=None):
        self.rows = rows or [definition()]
        self.archive = archive() if bundle is None else bundle
        self.identity = {"git_user_name": "enyst", "id": ORG, "org_id": ORG}
        self.calls = []
        self.detail_calls = 0
        self.change_after = False

    def json(self, path):
        self.calls.append(path)
        if path == "/api/v1/users/me":
            return copy.deepcopy(self.identity)
        if "?" in path:
            offset = int(path.split("offset=")[1])
            return {"automations": copy.deepcopy(self.rows[offset:offset+100]), "total": len(self.rows)}
        self.detail_calls += 1
        row = next(r for r in self.rows if path.endswith(r["id"]))
        result = copy.deepcopy(row)
        if self.change_after and self.detail_calls % 2 == 0:
            result["prompt"] = "Changed while downloading"
        return result

    def bundle(self, automation_id):
        if isinstance(self.archive, Exception):
            raise self.archive
        return self.archive


class ExportTests(unittest.TestCase):
    def test_complete_native_layout_executable_and_no_unknown_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder).resolve() / "cloud"
            result = exporter.export(FakeClient(), output, ORG)
            self.assertTrue(result["complete"])
            entry = output / ("automation-" + ID)
            metadata = json.loads((entry / "automation.yaml").read_text())
            self.assertFalse(metadata["enabled"])
            self.assertEqual(metadata["tarball_executables"], ["main.py"])
            self.assertNotIn("unknown_secret_payload", metadata)
            self.assertEqual((entry / "tarball/main.py").stat().st_mode & 0o777, 0o755)
            self.assertEqual(result["automations"][0]["id"], ID)
            self.assertEqual(result["automations"][0]["name"], "Attention")
            self.assertFalse(result["automations"][0]["enabled"])
            self.assertEqual(result["automations"][0]["agent_profile_id"], "astra-review-auditor")

    def test_identity_mismatch_stops_before_listing_or_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            client = FakeClient()
            client.identity["git_user_name"] = "somebody-else"
            with self.assertRaisesRegex(exporter.ExportError, "wrong_account"):
                exporter.export(client, Path(folder).resolve() / "cloud", ORG)
            self.assertEqual(client.calls, ["/api/v1/users/me"])
            self.assertFalse((Path(folder).resolve() / "cloud").exists())
            client.identity["git_user_name"] = "enyst"
            with self.assertRaisesRegex(exporter.ExportError, "wrong_org"):
                exporter.export(client, Path(folder).resolve() / "cloud", OTHER)

    def test_pagination_and_other_owner_filter(self):
        rows = [definition(str(exporter.uuid.UUID(int=n+1))) for n in range(205)]
        self.assertEqual(len(exporter.list_all(FakeClient(rows))), 205)
        with tempfile.TemporaryDirectory() as folder:
            foreign = definition(OTHER)
            foreign["user_id"] = OTHER
            client = FakeClient([definition(), foreign])
            report = exporter.export(client, Path(folder).resolve(), ORG)
            self.assertEqual(report["exported_owner_definitions"], 1)
            self.assertEqual(report["skipped_other_owners"], 1)

    def test_pagination_duplicate_fails(self):
        client = FakeClient()
        client.rows = [definition()] * 101
        with self.assertRaisesRegex(exporter.ExportError, "duplicate_page_entry"):
            exporter.list_all(client)

    def test_links_traversal_duplicates_and_collision_rejected(self):
        cases = [
            [("../outside", b"x", 0o644, "file")],
            [("/absolute", b"x", 0o644, "file")],
            [("link", b"", 0o777, "link")],
            [("same", b"a", 0o644, "file"), ("same", b"b", 0o644, "file")],
            [("parent", b"a", 0o644, "file"), ("parent/child", b"b", 0o644, "file")],
        ]
        for members in cases:
            with self.subTest(members=members), self.assertRaises(exporter.ExportError):
                exporter.unpack(archive(members))

    def test_size_limit_and_nested_archive_rejected(self):
        old_limit = exporter.MAX_FILE
        try:
            exporter.MAX_FILE = 2
            with self.assertRaisesRegex(exporter.ExportError, "archive_file_too_large"):
                exporter.unpack(archive())
        finally:
            exporter.MAX_FILE = old_limit
        with self.assertRaisesRegex(exporter.ExportError, "opaque_nested_content"):
            exporter.unpack(archive([("hidden.zip", b"opaque", 0o644, "file")]))

    def test_credentials_abort_whole_export_before_any_write(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder).resolve() / "cloud"
            bad = archive([("settings.py", ("ghp_" + "a" * 36).encode(), 0o644, "file")])
            with self.assertRaisesRegex(exporter.ExportError, "credential_like_content"):
                exporter.export(FakeClient(bundle=bad), output, ORG)
            self.assertFalse(output.exists())
            row = definition()
            row["preset_metadata"] = {"api_key": "not-for-export"}
            with self.assertRaisesRegex(exporter.ExportError, "secret_metadata_field"):
                exporter.export(FakeClient([row]), output, ORG)
            self.assertFalse(output.exists())

    def test_failed_download_preserves_existing_good_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder).resolve()
            exporter.export(FakeClient(), output, ORG)
            entry = output / ("automation-" + ID)
            before = {p.relative_to(entry).as_posix(): p.read_bytes()
                      for p in entry.rglob("*") if p.is_file() and p.name != "export-status.json"}
            report = exporter.export(FakeClient(bundle=exporter.ExportError("http_500")), output, ORG)
            after = {p.relative_to(entry).as_posix(): p.read_bytes()
                     for p in entry.rglob("*") if p.is_file() and p.name != "export-status.json"}
            self.assertEqual(before, after)
            self.assertFalse(report["complete"])
            self.assertTrue(report["automations"][0]["preserved_existing"])

    def test_first_failure_has_explicit_placeholder_not_importable_yaml(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder).resolve()
            report = exporter.export(FakeClient(bundle=exporter.ExportError("http_500")), output, ORG)
            entry = output / ("automation-" + ID)
            self.assertFalse((entry / "automation.yaml").exists())
            self.assertTrue((entry / "definition.incomplete.json").exists())
            self.assertEqual(report["automations"][0]["error"], "http_500")

    def test_changed_definition_is_incomplete(self):
        with tempfile.TemporaryDirectory() as folder:
            client = FakeClient()
            client.change_after = True
            report = exporter.export(client, Path(folder).resolve(), ORG)
            self.assertFalse(report["complete"])
            self.assertEqual(report["automations"][0]["error"], "definition_changed_during_export")
            self.assertFalse((Path(folder).resolve() / ("automation-" + ID) / "automation.yaml").exists())

    def test_runtime_timestamp_churn_completes_and_keeps_observations(self):
        class PollingClient(FakeClient):
            def json(self, path):
                response = super().json(path)
                tick = str(len(self.calls))
                rows = response["automations"] if "?" in path else [response]
                if path != "/api/v1/users/me":
                    for row in rows:
                        row["updated_at"] = "poll-" + tick
                        row["last_polled_at"] = "poll-" + tick
                        row["last_triggered_at"] = "run-" + tick
                return response
        with tempfile.TemporaryDirectory() as folder:
            report = exporter.export(PollingClient(), Path(folder).resolve(), ORG)
            self.assertTrue(report["complete"])
            receipt = report["automations"][0]
            self.assertNotEqual(receipt["updated_at"], receipt["updated_at_after_download"])

    def test_each_semantic_change_during_download_rejects_bundle(self):
        changes = {
            "name": "Renamed", "model": "another-profile",
            "trigger": {"type": "cron", "schedule": "0 3 * * *"},
            "setup_script_path": "new-setup.sh", "entrypoint": "python other.py",
            "timeout": 900, "keep_alive": True, "enabled": True,
            "prompt": "Different task", "preset_metadata": {"kind": "changed"},
            "agent_profile_id": "other-agent",
            "tarball_path": "oh-internal://uploads/replaced",
            "user_id": OTHER, "org_id": OTHER, "id": OTHER,
        }
        for key, value in changes.items():
            class ChangedClient(FakeClient):
                def json(self, path):
                    response = super().json(path)
                    if path == exporter.API + "/" + ID and self.detail_calls == 2:
                        response[key] = value
                        # Deliberately unchanged timestamp: content, not time, must guard.
                    return response
            with self.subTest(field=key), tempfile.TemporaryDirectory() as folder:
                output = Path(folder).resolve()
                report = exporter.export(ChangedClient(), output, ORG)
                self.assertFalse(report["complete"])
                self.assertEqual(report["automations"][0]["status"], "incomplete")
                self.assertFalse((output / ("automation-" + ID) / "automation.yaml").exists())

    def test_final_listing_content_changes_even_with_same_timestamp_abort_before_write(self):
        for change in ("name", "enabled", "user_id", "org_id", "tarball_path", "agent_profile_id",
                       "added", "removed"):
            class ChangedListing(FakeClient):
                def json(self, path):
                    response = super().json(path)
                    if "?" in path and self.detail_calls >= 2:
                        if change == "added":
                            response["automations"].append(definition(OTHER))
                            response["total"] += 1
                        elif change == "removed":
                            response["automations"] = []
                            response["total"] = 0
                        else:
                            response["automations"][0][change] = True if change == "enabled" else OTHER
                    return response
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                output = Path(folder).resolve() / "cloud"
                with self.assertRaisesRegex(exporter.ExportError, "listing_changed"):
                    exporter.export(ChangedListing(), output, ORG)
                self.assertFalse(output.exists())

    def test_listing_to_detail_change_is_not_exported(self):
        class ChangedBefore(FakeClient):
            def json(self, path):
                response = super().json(path)
                if path == exporter.API + "/" + ID:
                    response["prompt"] = "Changed before the first detailed read"
                return response
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder).resolve()
            report = exporter.export(ChangedBefore(), output, ORG)
            self.assertFalse(report["complete"])
            self.assertEqual(report["automations"][0]["error"], "definition_changed_during_export")
            self.assertFalse((output / ("automation-" + ID) / "automation.yaml").exists())

    def test_recovery_requires_pointer_and_digest_and_exports_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            archive_path = Path(folder).resolve() / "recovery.tgz"
            archive_path.write_bytes(archive())
            recovery = {ID: {"path": str(archive_path), "sha256": exporter.digest(archive_path.read_bytes()),
                             "tarball_path": definition()["tarball_path"],
                             "provenance": {"deployment_receipt_sha256": "a" * 64}}}
            client = FakeClient(bundle=exporter.ExportError("http_500"))
            report = exporter.export(client, Path(folder).resolve() / "out", ORG, recoveries=recovery)
            self.assertTrue(report["complete"])
            self.assertEqual(report["automations"][0]["bundle"]["kind"], "verified_local_archive")
            self.assertNotIn(str(archive_path), json.dumps(report))
            recovery[ID]["tarball_path"] = "oh-internal://wrong"
            report = exporter.export(client, Path(folder).resolve() / "out", ORG, recoveries=recovery)
            self.assertEqual(report["automations"][0]["error"], "recovery_pointer_mismatch")
            recovery[ID]["tarball_path"] = definition()["tarball_path"]
            recovery[ID]["sha256"] = "0" * 64
            report = exporter.export(client, Path(folder).resolve() / "out", ORG, recoveries=recovery)
            self.assertEqual(report["automations"][0]["error"], "recovery_hash_mismatch")

    def test_output_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder).resolve() / "real"
            target.mkdir()
            link = Path(folder).resolve() / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(exporter.ExportError, "output_symlink"):
                exporter.export(FakeClient(), link, ORG)
            self.assertEqual(list(target.iterdir()), [])

    def test_transport_refuses_redirect_and_only_builds_get(self):
        client = exporter.Client("dummy-credential")
        seen = []
        class Opener:
            def open(self, req, timeout):
                seen.append(req)
                raise urllib.error.HTTPError(req.full_url, 302, "", {}, None)
        client.opener = Opener()
        with self.assertRaisesRegex(exporter.ExportError, "http_302"):
            client.json("/api/v1/users/me")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].get_method(), "GET")
        self.assertEqual(seen[0].host, "app.all-hands.dev")
        self.assertIsNone(exporter.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test"))

if __name__ == "__main__":
    unittest.main()
