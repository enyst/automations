#!/usr/bin/env python3
"""Explicit, credential-safe deployment commands for Jev Fast Audit in Cloud.

Nothing happens on import. Credentials are read from Keychain only inside a
requested command and never printed, written into the bundle, or saved to Git.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
CLOUD = "https://app.all-hands.dev"
API = "/api/automation/v1"
OWNER = "18881862-24a9-4521-ba8a-8424314c6458"
SECRETS = ("TYPESAFE_API_KEY", "ENYST_GH_TOKEN")
DRAFT_FIELDS = {"name", "trigger", "entrypoint", "setup_script_path", "timeout", "keep_alive"}
SENSITIVE = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|\bsk-(?:oh-|proj-|ant-)?[A-Za-z0-9_-]{20,}")


class DeploymentError(Exception):
    """Only closed error codes are exposed to users and logs."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def keychain(name: str) -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", "openhands", "-a", name, "-w"],
        capture_output=True, text=True,
    )
    if result.returncode or not result.stdout.strip():
        raise DeploymentError("keychain_item_unavailable:" + name)
    return result.stdout.strip()


class Client:
    def __init__(self, host: str, credential: str):
        self.host = host
        self.credential = credential
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method: str, path: str, body=None, content_type="application/json"):
        if not path.startswith("/") or path.startswith("//"):
            raise DeploymentError("invalid_api_path")
        data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.host + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.credential,
            "Accept": "application/json",
            "Content-Type": content_type,
            "User-Agent": "enyst-jev-fast-audit-deployer",
        })
        try:
            with self.opener.open(req, timeout=60) as response:
                payload = response.read(8 * 1024 * 1024 + 1)
                if len(payload) > 8 * 1024 * 1024:
                    raise DeploymentError("response_too_large")
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as error:
            raise DeploymentError("http_" + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise DeploymentError("network_unavailable") from None
        except (UnicodeDecodeError, ValueError):
            raise DeploymentError("invalid_response") from None


def identity(cloud: Client) -> None:
    user = cloud.request("GET", "/api/v1/users/me")
    if user.get("git_user_name") != "enyst" or user.get("id") != OWNER or user.get("org_id") != OWNER:
        raise DeploymentError("wrong_cloud_identity")


def github_identity() -> Client:
    github = Client("https://api.github.com", keychain("ENYST_GH_TOKEN"))
    if github.request("GET", "/user").get("login") != "enyst":
        raise DeploymentError("wrong_github_identity")
    repo = github.request("GET", "/repos/enyst/automations")
    if repo.get("full_name") != "enyst/automations" or repo.get("private") is not True:
        raise DeploymentError("backup_repo_not_private")
    return github


def secret_names(cloud: Client) -> set[str]:
    names = set()
    page_id = None
    seen = set()
    while True:
        path = "/api/v1/secrets/search?limit=100"
        if page_id:
            path += "&page_id=" + urllib.parse.quote(page_id, safe="")
        page = cloud.request("GET", path)
        names.update(item["name"] for item in page["items"])
        page_id = page.get("next_page_id")
        if not page_id:
            return names
        if page_id in seen:
            raise DeploymentError("repeated_page")
        seen.add(page_id)


def read_definition(path: Path) -> dict:
    definition = json.loads(path.read_text())
    if not isinstance(definition, dict) or set(definition) - DRAFT_FIELDS:
        raise DeploymentError("unsupported_definition_fields")
    if definition.get("name") != "Jev Fast Audit":
        raise DeploymentError("unexpected_automation_name")
    if not isinstance(definition.get("trigger"), dict) or not definition.get("entrypoint"):
        raise DeploymentError("missing_definition_fields")
    if definition["trigger"].get("type") not in ("event", "cron"):
        raise DeploymentError("unsupported_trigger")
    return definition


def bundle(source: Path, known_secrets=()) -> tuple[bytes, list[str]]:
    source = source.resolve(strict=True)
    stream = io.BytesIO()
    names = []
    total = 0
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if any(part in {".git", "__pycache__", ".pytest_cache"} for part in relative.parts) or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_symlink():
                raise DeploymentError("symlink_in_bundle")
            if path.is_dir():
                continue
            if not path.is_file() or path.name.startswith(".env"):
                raise DeploymentError("unsupported_bundle_file")
            data = path.read_bytes()
            total += len(data)
            if total > 8 * 1024 * 1024:
                raise DeploymentError("bundle_unpacked_too_large")
            if SENSITIVE.search(data) or any(value and value.encode() in data for value in known_secrets):
                raise DeploymentError("credential_in_bundle")
            name = relative.as_posix()
            if path.suffix == ".py":
                compile(data, name, "exec")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
            names.append(name)
    if not names:
        raise DeploymentError("empty_bundle")
    result = gzip.compress(stream.getvalue(), mtime=0)
    if len(result) > 1024 * 1024:
        raise DeploymentError("bundle_upload_too_large")
    return result, names


def owned(cloud: Client, identifier: str) -> dict:
    try:
        identifier = str(uuid.UUID(identifier))
    except ValueError:
        raise DeploymentError("invalid_automation_id") from None
    current = cloud.request("GET", API + "/" + identifier)
    if current.get("name") != "Jev Fast Audit" or current.get("user_id") != OWNER or current.get("org_id") != OWNER:
        raise DeploymentError("automation_identity_mismatch")
    return current


def brief(value: dict) -> dict:
    return {key: value.get(key) for key in ("id", "name", "enabled", "trigger", "entrypoint", "timeout", "keep_alive", "status", "created_at", "started_at", "completed_at", "sandbox_id", "bash_command_id", "conversation_id") if key in value}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    sub.add_parser("install-secrets")
    deploy = sub.add_parser("deploy")
    deploy.add_argument("--source", type=Path, default=ROOT / "sources/jev-fast-audit")
    deploy.add_argument("--definition", type=Path, required=True)
    deploy.add_argument("--automation-id")
    deploy.add_argument("--paused", action="store_true", help="Pause after deployment; new definitions must use the January 1 staging schedule")
    for command in ("dispatch", "status"):
        item = sub.add_parser(command)
        item.add_argument("--automation-id", required=True)
    args = parser.parse_args(argv)
    cloud = Client(CLOUD, keychain("OPENHANDS_API_KEY"))
    identity(cloud)
    if args.command == "preflight":
        github_identity()
        names = secret_names(cloud)
        capabilities = cloud.request("GET", API + "/capabilities")
        print(json.dumps({"account": "enyst", "repo": "enyst/automations", "private": True,
            "required_secret_names": list(SECRETS), "missing_secret_names": sorted(set(SECRETS) - names),
            "ready": capabilities.get("ready"), "triggerKinds": capabilities.get("triggerKinds"),
            "eventSources": capabilities.get("eventSources"), "eventTypes": capabilities.get("eventTypes")}))
    elif args.command == "install-secrets":
        github = github_identity()
        names = secret_names(cloud)
        installed = []
        for name in SECRETS:
            if name in names:
                continue
            value = github.credential if name == "ENYST_GH_TOKEN" else keychain(name)
            cloud.request("POST", "/api/v1/secrets", {"name": name, "value": value,
                "description": "Jev Fast Audit: " + ("TypeSafe classifier access" if name == "TYPESAFE_API_KEY" else "GitHub as enyst")})
            installed.append(name)
        if set(SECRETS) - secret_names(cloud):
            raise DeploymentError("secrets_verification_failed")
        print(json.dumps({"installed_secret_names": installed, "existing_preserved": sorted(set(SECRETS) - set(installed))}))
    elif args.command == "deploy":
        github = github_identity()
        if set(SECRETS) - secret_names(cloud):
            raise DeploymentError("required_cloud_secret_missing")
        definition = read_definition(args.definition)
        before = owned(cloud, args.automation_id) if args.automation_id else None
        if args.paused and before is None and definition["trigger"] != {"type": "cron", "schedule": "0 0 1 1 *", "timezone": "UTC"}:
            raise DeploymentError("paused_create_requires_staging_schedule")
        if before is None:
            listing = cloud.request("GET", API + "?limit=100")
            if listing.get("total", 0) > 100:
                raise DeploymentError("automation_inventory_needs_pagination")
            if any(row.get("name") == "Jev Fast Audit" for row in listing.get("automations", [])):
                raise DeploymentError("automation_exists_use_id")
        archive, files = bundle(args.source, (cloud.credential, github.credential))
        if "main.py" not in files or definition["entrypoint"] != "python3 main.py":
            raise DeploymentError("unexpected_entrypoint")
        upload = cloud.request("POST", API + "/uploads?name=Jev-Fast-Audit", archive, "application/gzip")
        if upload.get("status") != "COMPLETED" or not upload.get("tarball_path"):
            raise DeploymentError("upload_incomplete")
        desired = dict(definition, tarball_path=upload["tarball_path"])
        validation = cloud.request("POST", API + "/validate", {"endpoint": "/v1", "draft": desired})
        if validation.get("valid") is not True:
            codes = [item.get("code", "unknown") for item in validation.get("errors", [])]
            raise DeploymentError("definition_rejected:" + ",".join(codes))
        if before is None:
            result = cloud.request("POST", API, desired)
        else:
            result = cloud.request("PATCH", API + "/" + before["id"], desired)
        if args.paused:
            cloud.request("PATCH", API + "/" + result["id"], {"enabled": False})
        after = owned(cloud, result["id"])
        if any(after.get(key) != value for key, value in desired.items() if key != "trigger"):
            raise DeploymentError("deployment_verification_failed")
        if any(after["trigger"].get(key) != value for key, value in desired["trigger"].items()):
            raise DeploymentError("trigger_verification_failed")
        if args.paused and after.get("enabled") is not False:
            raise DeploymentError("pause_verification_failed")
        if before and not args.paused and after.get("enabled") != before.get("enabled"):
            raise DeploymentError("enabled_state_changed")
        print(json.dumps({"automation": brief(after), "bundle_sha256": hashlib.sha256(archive).hexdigest(), "bundle_files": files}))
    else:
        automation = owned(cloud, args.automation_id)
        if args.command == "dispatch":
            run = cloud.request("POST", API + "/" + automation["id"] + "/dispatch")
            print(json.dumps({"run": brief(run)}))
        else:
            runs = cloud.request("GET", API + "/" + automation["id"] + "/runs?limit=10")
            print(json.dumps({"automation": brief(automation), "runs": [brief(run) for run in runs.get("runs", [])]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeploymentError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(json.dumps({"error": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
