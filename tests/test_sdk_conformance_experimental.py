"""Offline checks for the co-located experimental SDK positive control."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock


SOURCE = Path(__file__).parents[1] / "sources/sdk-lifecycle-conformance"
sys.path.insert(0, str(SOURCE))
import prepare_experimental as prepare
import self_contained as experimental


def git(*args, cwd):
    return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.DEVNULL).decode().strip()


class ExperimentalSetupTests(unittest.TestCase):
    def test_local_fetch_requires_exact_revision_and_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            git("init", "--quiet", cwd=source)
            (source / "candidate.txt").write_text("pinned source\n")
            git("add", "candidate.txt", cwd=source)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                            "commit", "--quiet", "-m", "fixture"], cwd=source, check=True)
            revision = git("rev-parse", "HEAD", cwd=source)
            digest = prepare.archive_digest(source)
            destination = root / "candidate"
            identity = prepare.fetch_checkout(destination, str(source), revision, digest)
            self.assertEqual(identity, {"revision": revision, "archive_sha256": digest,
                                        "archive_verified": True})
            with self.assertRaisesRegex(prepare.PreparationError, "candidate_archive_mismatch"):
                prepare.attest_checkout(destination, revision, "0" * 64)
            (destination / "candidate.txt").write_text("changed\n")
            with self.assertRaisesRegex(prepare.PreparationError, "candidate_tracked_checkout_dirty"):
                prepare.attest_checkout(destination, revision, digest)

    def test_child_environment_does_not_forward_account_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            with patch.dict(os.environ, {"OPENHANDS_API_KEY": "private-account-token",
                                      "GITHUB_TOKEN": "private-github-token",
                                      "CONFORMANCE_FIXTURE_CONTROL_KEY": "private-control-token"}):
                child = experimental.child_environment(work, work / "server-config.json")
            self.assertFalse(set(child) & {"OPENHANDS_API_KEY", "GITHUB_TOKEN",
                                            "CONFORMANCE_FIXTURE_CONTROL_KEY", "SESSION_API_KEY"})
            self.assertNotIn("private-account-token", json.dumps(child))

    def test_setup_commands_strip_account_credentials_and_keep_network_settings(self):
        with (patch.dict(os.environ, {"OPENHANDS_API_KEY": "private-account-token",
                                   "SESSION_API_KEY": "private-session-token",
                                   "AUTOMATION_CALLBACK_URL": "private-callback",
                                   "HTTPS_PROXY": "http://trusted-proxy.invalid:8080",
                                   "SSL_CERT_FILE": "/trusted/ca.pem"}),
              patch.object(prepare, "subprocess") as process):
            process.run.return_value.returncode = 0
            process.run.return_value.stdout = b""
            prepare.run_command(["sh", "setup.sh"])
            environment = process.run.call_args.kwargs["env"]
        self.assertFalse(set(environment) & {"OPENHANDS_API_KEY", "SESSION_API_KEY",
                                             "AUTOMATION_CALLBACK_URL"})
        self.assertEqual(environment["HTTPS_PROXY"], "http://trusted-proxy.invalid:8080")
        self.assertEqual(environment["SSL_CERT_FILE"], "/trusted/ca.pem")

    def test_loopback_ports_are_distinct(self):
        reserved = set()

        class FakeSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                reserved.remove(self.port)

            def bind(self, address):
                self.port = next(port for port in (33001, 33002) if port not in reserved)
                reserved.add(self.port)

            def getsockname(self):
                return ("127.0.0.1", self.port)

        with patch.object(experimental.socket, "socket", side_effect=FakeSocket):
            target, peer = experimental.loopback_ports()
        self.assertNotEqual(target, peer)
        self.assertFalse(reserved)

    def test_import_assertion_rejects_ambient_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            for package in experimental.IMPORT_ROOTS.values():
                (repository / package).mkdir()
            ambient = repository / "site-packages/openhands/__init__.py"
            ambient.parent.mkdir(parents=True)
            ambient.write_text("")
            paths = {module: str(ambient)
                     for module in experimental.IMPORT_ROOTS}
            result = MagicMock(returncode=0, stdout=("PINNED_IMPORTS=" + json.dumps(paths)).encode())
            with patch.object(experimental.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(experimental.LocalRunError,
                                            "candidate_import_outside_pinned_checkout"):
                    experimental.resolved_imports(repository, Path(sys.executable), {})

    def test_failed_attestation_emits_complete_blocked_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "not-a-checkout"
            candidate.mkdir()
            output = root / "evidence.json"
            with (patch.object(experimental, "loopback_ports", return_value=(33001, 33002)),
                  patch.object(experimental.verifier, "callback")):
                code = experimental.run(candidate_repository=candidate,
                                        candidate_python=Path(sys.executable),
                                        evidence_path=output)
            self.assertEqual(code, 2)
            evidence = json.loads(output.read_text())
            self.assertEqual(evidence["execution_mode"], "co_located_positive_control")
            self.assertEqual(evidence["subject"]["artifact_kind"], "git_source_archive_tar")
            self.assertEqual(evidence["verdict"], "blocked")
            self.assertEqual(len(evidence["scenarios"]), 3)
            self.assertFalse(evidence["archive_verification"]["archive_verified"])
            config = evidence["effective_config"]
            expected = hashlib.sha256(json.dumps(config, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()
            self.assertEqual(evidence["configuration_sha256"], expected)
            self.assertIn("self_contained.py", evidence["bundle"]["files"])
            self.assertIn("setup_experimental.sh", evidence["bundle"]["files"])
            self.assertEqual(evidence["bundle"]["sha256"],
                             experimental.verifier.bundle_digest(experimental.EXPERIMENTAL_FILES))

    def test_experimental_digest_covers_preparer_setup_and_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in experimental.EXPERIMENTAL_FILES:
                shutil.copyfile(SOURCE / name, root / name)
            with patch.object(experimental.verifier, "ROOT", root):
                original = experimental.verifier.bundle_digest(experimental.EXPERIMENTAL_FILES)
                for name in ("prepare_experimental.py", "setup_experimental.sh", "self_contained.py"):
                    path = root / name
                    content = path.read_bytes()
                    path.write_bytes(content + b"\n# changed\n")
                    self.assertNotEqual(experimental.verifier.bundle_digest(
                        experimental.EXPERIMENTAL_FILES), original)
                    path.write_bytes(content)


if __name__ == "__main__":
    unittest.main()
