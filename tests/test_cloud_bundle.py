"""Real Git objects qualify snapshot preservation; mocked Cloud remains GET-only."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import cloud_bundle as bundles
import export_cloud as exporter
from test_export_cloud import FakeClient, ORG, ID, archive


class GitSnapshots(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.git("init", "-q")
        self.source = self.root / "sources/example"
        self.source.mkdir(parents=True)
        self.content = b"print('hello')\n"
        (self.source / "main.py").write_bytes(self.content)
        # Extra development files must not leak into restored runtime bundles.
        (self.source / "test_main.py").write_text("# not runtime\n")
        self.commit = self.save()
        self.reference = {"commit": self.commit, "path": "sources/example"}
        self.hashes = {"main.py": hashlib.sha256(self.content).hexdigest()}
        self.output = self.root / "cloud-automations"
        self.directory = self.output / ("automation-" + ID)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL).decode().strip()

    def save(self):
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        commit = self.git("rev-parse", "HEAD")
        self.git("tag", "snapshot-" + commit)
        return commit

    def export(self, content=None):
        client = FakeClient(bundle=archive([("main.py", self.content if content is None else content, 0o755, "file")]))
        return exporter.export(client, self.output, ORG, repo_root=self.root)

    def test_export_pins_committed_source_and_restores_only_observed_files_with_modes(self):
        report = self.export()
        receipt = report["automations"][0]
        self.assertEqual(receipt["source"], self.reference)
        self.assertFalse((self.directory / "tarball").exists())
        restored = self.root / "restored"
        self.assertEqual(bundles.materialize(self.root, self.directory, restored), 1)
        self.assertEqual((restored / "tarball/main.py").read_bytes(), self.content)
        self.assertEqual((restored / "tarball/main.py").stat().st_mode & 0o777, 0o755)
        self.assertFalse((restored / "tarball/test_main.py").exists())
        self.assertEqual((restored / "automation.yaml").read_bytes(), (self.directory / "automation.yaml").read_bytes())

    def test_source_edits_and_later_commits_do_not_change_snapshot_or_repeat_export(self):
        self.export()
        (self.source / "main.py").write_bytes(b"print('new')\n")
        self.save()
        self.assertEqual(bundles.read_export(self.root, self.directory), {"main.py": self.content})
        self.assertEqual(self.export()["automations"][0]["source"], self.reference)

    def test_new_cloud_bytes_are_retained_inline_until_committed(self):
        self.export()
        changed = b"print('Cloud changed first')\n"
        receipt = self.export(changed)["automations"][0]
        self.assertNotIn("source", receipt)
        self.assertEqual(bundles.read_export(self.root, self.directory), {"main.py": changed})
        commit = self.save()
        receipt = self.export(changed)["automations"][0]
        self.assertEqual(receipt["source"], {"commit": commit, "path": "cloud-automations/automation-" + ID + "/tarball"})
        self.assertFalse((self.directory / "tarball").exists())
        self.assertEqual(bundles.read_export(self.root, self.directory), {"main.py": changed})

    def test_uncommitted_source_is_not_used_as_snapshot(self):
        changed = b"print('uncommitted')\n"
        (self.source / "main.py").write_bytes(changed)
        self.assertNotIn("source", self.export(changed)["automations"][0])

    def test_untagged_pr_head_is_not_used_as_snapshot(self):
        self.git("tag", "-d", "snapshot-" + self.commit)
        self.assertNotIn("source", self.export()["automations"][0])

    def test_failed_download_preserves_reference(self):
        self.export()
        report = exporter.export(FakeClient(bundle=exporter.ExportError("http_500")), self.output, ORG, repo_root=self.root)
        self.assertFalse(report["complete"])
        self.assertTrue(report["automations"][0]["preserved_existing"])
        self.assertEqual(bundles.read_export(self.root, self.directory), {"main.py": self.content})

    def test_bad_hash_missing_commit_symlink_and_unsafe_path_fail_before_restore(self):
        self.export()
        bad_hash = {"main.py": "0" * 64}
        with self.assertRaisesRegex(bundles.BundleError, "bundle_hash_mismatch"):
            bundles.source_files(self.root, self.reference, bad_hash)
        with self.assertRaisesRegex(bundles.BundleError, "bundle_git_object_unavailable"):
            bundles.source_files(self.root, {**self.reference, "commit": "f" * 40}, self.hashes)
        (self.source / "main.py").unlink()
        (self.source / "main.py").symlink_to("test_main.py")
        commit = self.save()
        with self.assertRaisesRegex(bundles.BundleError, "bundle_file_not_regular"):
            bundles.source_files(self.root, {**self.reference, "commit": commit}, self.hashes)
        for name in ("../escape", "/absolute", "./main.py", "a\\b"):
            with self.subTest(name=name), self.assertRaises(bundles.BundleError):
                bundles.source_files(self.root, self.reference, {name: "a" * 64})
        receipt = json.loads((self.directory / "export-status.json").read_text())
        receipt["files"] = bad_hash
        (self.directory / "export-status.json").write_text(json.dumps(receipt))
        restored = self.root / "restored"
        with self.assertRaises(bundles.BundleError):
            bundles.materialize(self.root, self.directory, restored)
        self.assertFalse(restored.exists())

    def test_restore_does_not_overwrite_existing_directory_or_follow_output_symlinks(self):
        self.export()
        target = self.root / "existing"
        target.mkdir()
        (target / "keep").write_text("keep")
        with self.assertRaises(FileExistsError):
            bundles.materialize(self.root, self.directory, target)
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(bundles.BundleError, "output_symlink"):
            bundles.materialize(self.root, self.directory, link / "child")
        self.assertEqual((target / "keep").read_text(), "keep")


class RepositorySnapshots(unittest.TestCase):
    def test_every_recorded_snapshot_is_recoverable_and_matches_manifest(self):
        root = Path(__file__).parents[1]
        cloud = root / "cloud-automations"
        manifest = json.loads((cloud / "manifest.json").read_text())
        indexed = {row["id"]: row for row in manifest["automations"]}
        for directory in cloud.glob("automation-*"):
            with self.subTest(directory=directory.name):
                receipt = json.loads((directory / "export-status.json").read_text())
                if receipt.get("status") != "complete":
                    continue
                files = bundles.read_export(root, directory)
                self.assertEqual({name: hashlib.sha256(data).hexdigest() for name, data in files.items()}, receipt["files"])
                if "source" in receipt:
                    retained = bundles.git(root, "for-each-ref", "--contains=" + receipt["source"]["commit"],
                                           "--format=%(refname)", "refs/tags", "refs/remotes/origin/main")
                    self.assertTrue(retained, "Snapshot commit must survive PR branch deletion")
                if receipt["id"] in indexed:
                    self.assertEqual(indexed[receipt["id"]], receipt)


if __name__ == "__main__":
    unittest.main()
