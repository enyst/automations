#!/usr/bin/env python3
"""Development smoke against a real Agent Server subprocess and real HTTP peer.

This is a positive control, NOT a security sandbox or authenticated admission.
Run only a trusted checkout on a credential-free development host. Production
Cloud must use separately isolated candidate infrastructure instead.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

SOURCE = Path(__file__).resolve().parents[1] / "sources/sdk-lifecycle-conformance"
sys.path.insert(0, str(SOURCE))
import fixture
import main as verifier


def port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def smoke(args):
    repository = args.candidate_repository.resolve(strict=True)
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repository).strip():
        raise RuntimeError("candidate_tracked_checkout_is_dirty")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=tar", revision], cwd=repository)
    artifact = hashlib.sha256(archive).hexdigest()
    with tempfile.TemporaryDirectory(prefix="sdk-conformance-") as temporary:
        work = Path(temporary)
        config_path = work / "server-config.json"
        config_path.write_text(json.dumps({"session_api_keys": ["synthetic-candidate-session-key"],
            "secret_key": "synthetic-local-persistence-key", "conversations_path": str(work / "conversations"),
            "workspace_path": str(work / "workspace"), "enable_vscode": False,
            "preload_tools": False, "enable_browser": False}))
        target_port, fixture_port = port(), port()
        target = "http://127.0.0.1:" + str(target_port)
        peer = fixture.FixtureServer(("127.0.0.1", fixture_port))
        threading.Thread(target=peer.serve_forever, daemon=True).start()
        # Explicit environment: inherited Cloud/GitHub/account/LLM credentials
        # are never forwarded to candidate code. This does not replace isolation.
        env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "LANG": "C.UTF-8",
               "OPENHANDS_AGENT_SERVER_CONFIG_PATH": str(config_path),
               "OH_PERSISTENCE_DIR": str(work / "persistence"),
               "LITELLM_LOCAL_MODEL_COST_MAP": "True", "OPENHANDS_DISABLE_TELEMETRY": "1"}
        processes = []
        log = (work / "server.log").open("wb")

        def start():
            process = subprocess.Popen([str(args.candidate_python.absolute()), "-m", "openhands.agent_server", "--host", "127.0.0.1", "--port", str(target_port)], cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            http = verifier.HTTP(target, "X-Session-API-Key", "synthetic-candidate-session-key")
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("candidate_start_failed")
                try:
                    http.request("GET", "/ready")
                    return process
                except verifier.Blocked:
                    time.sleep(0.2)
            raise RuntimeError("candidate_start_timeout")

        def restart():
            # Process-crash recovery, not a clean shutdown simulation. Exact
            # same config/storage and same executable are used for the restart.
            processes[-1].kill()
            processes[-1].wait(timeout=10)
            start()
            return {"restarted": True, "storage_preserved": True}

        try:
            start()
            os.environ["CONFORMANCE_CANDIDATE_SESSION_KEY"] = "synthetic-candidate-session-key"
            config = {"candidate_url": target, "candidate_revision": revision,
                      "candidate_artifact_sha256": artifact, "candidate_binding_receipt": "local-development-smoke-unattested",
                      "fixture_url": "http://127.0.0.1:" + str(fixture_port),
                      "working_dir": str(work / "project"), "timeout_seconds": 30}
            evidence = verifier.run(config, restart=restart)
            evidence["execution_mode"] = "local positive control; no malicious-candidate isolation"
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
            if evidence["verdict"] != "pass":
                # Synthetic-only development logs, never produced by Cloud.
                args.evidence.with_suffix(".server.log").write_bytes((work / "server.log").read_bytes())
            print(json.dumps({k: evidence[k] for k in ("subject", "bundle", "scenarios", "verdict")}))
            return {"pass": 0, "fail": 1, "blocked": 2}[evidence["verdict"]]
        finally:
            if not args.evidence.exists():
                args.evidence.parent.mkdir(parents=True, exist_ok=True)
                args.evidence.with_suffix(".server.log").write_bytes((work / "server.log").read_bytes())
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
            peer.shutdown()
            peer.server_close()
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-python", required=True, type=Path)
    parser.add_argument("--candidate-repository", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    raise SystemExit(smoke(parser.parse_args()))
