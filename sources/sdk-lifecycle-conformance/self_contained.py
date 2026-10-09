"""Experimental Cloud positive control with a pinned same-sandbox SDK server.

The candidate is trusted, pinned source in a child process. This is not a
malicious-candidate isolation boundary or a merge-admission result.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
import uuid

import fixture
import main as verifier
from prepare_experimental import (CHECKOUT, SDK_ARCHIVE_SHA256, SDK_REVISION,
                                  PreparationError, attest_checkout)


EXPERIMENTAL_FILES = (
    "main.py", "fixture.py", "contracts.json", "requirements.txt",
    "prepare_experimental.py", "setup_experimental.sh", "run_experimental.sh",
    "self_contained.py")
EXECUTION_MODE = "co_located_positive_control"
IMPORT_ROOTS = {
    "openhands.agent_server": "openhands-agent-server",
    "openhands.sdk": "openhands-sdk",
    "openhands.tools": "openhands-tools",
    "openhands.workspace": "openhands-workspace",
}


class LocalRunError(Exception):
    pass


def loopback_ports():
    with socket.socket() as target, socket.socket() as peer:
        target.bind(("127.0.0.1", 0))
        peer.bind(("127.0.0.1", 0))
        return target.getsockname()[1], peer.getsockname()[1]


def child_environment(work, config_path):
    # Deliberately omit inherited Cloud, GitHub, provider, and fixture keys.
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(work),
            "LANG": "C.UTF-8", "OPENHANDS_AGENT_SERVER_CONFIG_PATH": str(config_path),
            "OH_PERSISTENCE_DIR": str(work / "persistence"),
            "LITELLM_LOCAL_MODEL_COST_MAP": "True", "OPENHANDS_DISABLE_TELEMETRY": "1",
            "OPENHANDS_SUPPRESS_BANNER": "1"}


def resolved_imports(repository, python, environment):
    code = ("import importlib, json; names = " + repr(list(IMPORT_ROOTS)) +
            "; print('PINNED_IMPORTS=' + json.dumps({n: importlib.import_module(n).__file__ for n in names}))")
    try:
        result = subprocess.run([str(python), "-c", code], cwd=repository,
                                env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=45, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise LocalRunError("candidate_import_check_unavailable") from None
    if result.returncode:
        raise LocalRunError("candidate_import_check_failed")
    lines = [line.removeprefix("PINNED_IMPORTS=") for line in result.stdout.decode(errors="replace").splitlines()
             if line.startswith("PINNED_IMPORTS=")]
    if len(lines) != 1:
        raise LocalRunError("candidate_import_check_missing")
    try:
        paths = json.loads(lines[0])
        relative = {}
        for module, package in IMPORT_ROOTS.items():
            module_path = Path(paths[module]).resolve(strict=True)
            expected = (repository / package).resolve(strict=True)
            if not module_path.is_relative_to(expected):
                raise LocalRunError("candidate_import_outside_pinned_checkout")
            relative[module] = str(module_path.relative_to(repository.resolve()))
        return relative
    except (KeyError, ValueError, TypeError, OSError):
        raise LocalRunError("candidate_import_check_invalid") from None


@contextmanager
def synthetic_capabilities(candidate_key, fixture_key):
    names = {"CONFORMANCE_CANDIDATE_SESSION_KEY": candidate_key,
             "CONFORMANCE_FIXTURE_CONTROL_KEY": fixture_key}
    prior = {name: os.environ.get(name) for name in names}
    try:
        os.environ.update(names)
        yield
    finally:
        for name, old in prior.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old


def blocked_evidence(config, code):
    contract = json.loads((verifier.ROOT / "contracts.json").read_text())
    return {"schema_version": 1,
            "subject": {"revision": SDK_REVISION, "artifact_sha256": SDK_ARCHIVE_SHA256},
            "bundle": {"id": contract["id"],
                       "sha256": verifier.bundle_digest(EXPERIMENTAL_FILES)},
            "configuration_sha256": hashlib.sha256(json.dumps(config, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            "binding_receipt": config["candidate_binding_receipt"],
            "scenarios": [{"id": item["id"], "status": "blocked", "details": {"code": code}}
                          for item in contract["scenarios"]],
            "verdict": "blocked"}


def run(candidate_repository=None, candidate_python=None, evidence_path=None):
    repository = (Path(candidate_repository) if candidate_repository is not None
                  else Path.cwd() / CHECKOUT).resolve()
    python = (Path(candidate_python) if candidate_python is not None
              else repository / ".venv/bin/python").absolute()
    output = Path(evidence_path) if evidence_path is not None else Path.cwd() / "evidence.json"
    work = output.parent / (".experimental-run-" + uuid.uuid4().hex)
    work.mkdir(mode=0o700)
    config_path = work / "server-config.json"
    candidate_key = secrets.token_urlsafe(32)
    fixture_key = secrets.token_urlsafe(32)
    config_path.write_text(json.dumps({"session_api_keys": [candidate_key],
        "secret_key": secrets.token_urlsafe(32), "conversations_path": str(work / "conversations"),
        "workspace_path": str(work / "workspace"), "enable_vscode": False,
        "preload_tools": False, "enable_browser": False}))
    target_port, fixture_port = loopback_ports()
    config = {"candidate_url": "http://127.0.0.1:" + str(target_port),
              "candidate_revision": SDK_REVISION,
              "candidate_artifact_sha256": SDK_ARCHIVE_SHA256,
              "candidate_binding_receipt": "experimental-pinned-source-same-sandbox",
              "fixture_url": "http://127.0.0.1:" + str(fixture_port),
              "restart_control_url": "", "working_dir": str(work / "project"),
              "timeout_seconds": 45}
    child_env = child_environment(work, config_path)
    identity = None
    imports = None
    evidence = None
    processes = []
    peer = None
    log = None

    def start():
        try:
            process = subprocess.Popen([str(python), "-m", "openhands.agent_server",
                "--host", "127.0.0.1", "--port", str(target_port)], cwd=work,
                env=child_env, stdout=log, stderr=subprocess.STDOUT)
        except OSError:
            raise LocalRunError("candidate_start_unavailable") from None
        processes.append(process)
        target = verifier.HTTP(config["candidate_url"], "X-Session-API-Key", candidate_key)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise LocalRunError("candidate_start_failed")
            try:
                target.request("GET", "/ready")
                return process
            except verifier.Blocked:
                time.sleep(0.2)
        raise LocalRunError("candidate_start_timeout")

    def restart():
        processes[-1].kill()
        processes[-1].wait(timeout=10)
        start()
        return {"restarted": True, "storage_preserved": True}

    try:
        identity = attest_checkout(repository)
        if not python.is_file():
            raise LocalRunError("candidate_python_missing")
        imports = resolved_imports(repository, python, child_env)
        peer = fixture.FixtureServer(("127.0.0.1", fixture_port), fixture_key)
        threading.Thread(target=peer.serve_forever, daemon=True).start()
        log = (work / "server.log").open("wb")
        start()
        with synthetic_capabilities(candidate_key, fixture_key):
            evidence = verifier.run(config, restart=restart,
                                    bundle_files=EXPERIMENTAL_FILES)
    except PreparationError as exc:
        evidence = blocked_evidence(config, str(exc))
    except LocalRunError as exc:
        evidence = blocked_evidence(config, str(exc))
    except Exception:
        evidence = blocked_evidence(config, "experimental_launcher_failed")
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        if peer is not None:
            peer.shutdown()
            peer.server_close()
        if log is not None:
            log.close()

    evidence["execution_mode"] = EXECUTION_MODE
    evidence["subject"]["artifact_kind"] = "git_source_archive_tar"
    evidence["runtime_isolation"] = "same-sandbox-subprocess; trusted pinned source only"
    evidence["archive_verification"] = identity or {"revision": SDK_REVISION,
        "archive_sha256": SDK_ARCHIVE_SHA256, "archive_verified": False}
    evidence["candidate_imports_verified"] = imports is not None
    if imports is not None:
        evidence["candidate_imports"] = imports
    evidence["effective_config"] = config
    evidence["bundle"]["files"] = list(EXPERIMENTAL_FILES)
    evidence["experimental_run_directory"] = str(work)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(verifier.redact(evidence), indent=2) + "\n")
    print(json.dumps({key: evidence[key] for key in ("execution_mode", "subject", "bundle", "scenarios", "verdict")}))
    try:
        verifier.callback(evidence)
    except (verifier.Blocked, verifier.Violation) as exc:
        print(json.dumps({"callback": "unavailable", "code": str(exc)}))
    except Exception:
        print(json.dumps({"callback": "unavailable", "code": "unexpected_callback_error"}))
    return {"pass": 0, "fail": 1, "blocked": 2}[evidence["verdict"]]


if __name__ == "__main__":
    raise SystemExit(run())
