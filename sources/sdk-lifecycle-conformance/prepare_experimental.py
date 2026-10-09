"""Prepare one pinned SDK checkout for an experimental same-sandbox run."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


SDK_REMOTE = "https://github.com/OpenHands/software-agent-sdk.git"
SDK_REVISION = "9f47d471ee6f91ba1d140d10d34f3b26d5ac2427"
SDK_ARCHIVE_SHA256 = "5ef7a4c11ecd6c8a0a0f47e3bed0da5ce9fc0be05d5d5446d5a930c1d9440909"
UV_VERSION = "0.11.19"
CHECKOUT = Path(".candidate-sdk")
SETUP_HOME = tempfile.TemporaryDirectory(prefix="sdk-conformance-setup-")
SAFE_ENVIRONMENT_KEYS = (
    "PATH", "LANG", "LC_ALL", "TMPDIR", "HTTP_PROXY", "HTTPS_PROXY",
    "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy",
    "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE", "GIT_SSL_CAINFO", "PIP_CERT", "UV_NATIVE_TLS",
)


class PreparationError(Exception):
    pass


def setup_environment():
    """Pass only network/trust settings to Git, pip, uv, and build hooks."""
    environment = {key: os.environ[key] for key in SAFE_ENVIRONMENT_KEYS
                   if key in os.environ}
    environment["HOME"] = SETUP_HOME.name
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_ASKPASS"] = "/bin/false"
    return environment


def run_command(args, *, cwd=None, code="command_failed"):
    try:
        result = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, check=False,
                                env=setup_environment())
    except OSError:
        raise PreparationError(code) from None
    if result.returncode:
        raise PreparationError(code)
    return result.stdout


def archive_digest(repository):
    digest = hashlib.sha256()
    try:
        process = subprocess.Popen(["git", "archive", "--format=tar", "HEAD"],
                                   cwd=repository, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,
                                   env=setup_environment())
    except OSError:
        raise PreparationError("candidate_archive_failed") from None
    assert process.stdout is not None
    while chunk := process.stdout.read(1024 * 1024):
        digest.update(chunk)
    process.stdout.close()
    if process.wait() != 0:
        raise PreparationError("candidate_archive_failed")
    return digest.hexdigest()


def attest_checkout(repository, revision=SDK_REVISION, archive_sha256=SDK_ARCHIVE_SHA256):
    observed = run_command(["git", "rev-parse", "HEAD"], cwd=repository,
                           code="candidate_revision_unavailable").decode().strip()
    if observed != revision:
        raise PreparationError("candidate_revision_mismatch")
    dirty = run_command(["git", "status", "--porcelain", "--untracked-files=no"],
                        cwd=repository, code="candidate_status_unavailable")
    if dirty.strip():
        raise PreparationError("candidate_tracked_checkout_dirty")
    if archive_digest(repository) != archive_sha256:
        raise PreparationError("candidate_archive_mismatch")
    return {"revision": observed, "archive_sha256": archive_sha256,
            "archive_verified": True}


def fetch_checkout(destination, remote=SDK_REMOTE, revision=SDK_REVISION,
                   archive_sha256=SDK_ARCHIVE_SHA256):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise PreparationError("candidate_checkout_already_exists")
    run_command(["git", "init", "--quiet", str(destination)], code="candidate_init_failed")
    run_command(["git", "remote", "add", "origin", remote], cwd=destination,
                code="candidate_remote_failed")
    run_command(["git", "fetch", "--depth=1", "origin", revision], cwd=destination,
                code="candidate_fetch_failed")
    run_command(["git", "checkout", "--detach", "--quiet", "FETCH_HEAD"],
                cwd=destination, code="candidate_checkout_failed")
    return attest_checkout(destination, revision, archive_sha256)


def uv_executable():
    installed = shutil.which("uv")
    if installed:
        return installed
    bootstrap = Path(".uv-bootstrap")
    run_command([sys.executable, "-m", "venv", str(bootstrap)],
                code="uv_bootstrap_venv_failed")
    python = bootstrap / "bin/python"
    run_command([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                 "uv==" + UV_VERSION], code="uv_bootstrap_install_failed")
    return str(bootstrap / "bin/uv")


def install_verifier():
    run_command([sys.executable, "-m", "venv", ".verifier-venv"],
                code="verifier_venv_install_failed")
    run_command([".verifier-venv/bin/python", "-m", "pip", "install",
                 "--disable-pip-version-check", "-r", "requirements.txt"],
                code="verifier_dependency_install_failed")


def install_agent_server(repository, uv=None):
    command = uv or uv_executable()
    run_command([command, "sync", "--frozen", "--no-dev", "--package",
                 "openhands-agent-server", "--python", "3.13"], cwd=repository,
                code="candidate_dependency_install_failed")
    python = Path(repository) / ".venv/bin/python"
    if not python.is_file():
        raise PreparationError("candidate_python_missing")
    return python


def main():
    try:
        install_verifier()
        identity = fetch_checkout(CHECKOUT)
        install_agent_server(CHECKOUT)
    except PreparationError as exc:
        print("experimental_setup_blocked:" + str(exc), file=sys.stderr)
        return 2
    print("experimental_setup_ready:" + identity["revision"] + ":" + identity["archive_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
