#!/usr/bin/env python3
"""Explicit Cloud registration/dispatch; env-only account bearer, no GitHub writes.

Dry-run performs no network access and requires no credential. Create always
registers inactive; this helper has no schedule-enabling or candidate execution
operation. Pushing Git does not deploy this bundle.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import uuid

# The existing transport refuses redirects and prints only fixed error codes.
from deploy_jev import Client, DeploymentError, OWNER, SENSITIVE

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cloud-automations/automation-481e4de6-48b1-46b3-a99b-985e7b35ebd3/tarball"
DEFINITION = ROOT / "definitions/sdk-lifecycle-conformance.json"
CLOUD = "https://app.all-hands.dev"
API = "/api/automation/v1"
FILES = ("main.py", "fixture.py", "contracts.json", "config.json", "requirements.txt", "setup.sh")


def package(config_path, credential=""):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in FILES:
            path = config_path if name == "config.json" else SOURCE / name
            if path.is_symlink() or not path.is_file():
                raise DeploymentError("unsupported_bundle_file")
            data = path.read_bytes()
            if SENSITIVE.search(data) or credential and credential.encode() in data:
                raise DeploymentError("credential_in_bundle")
            if name.endswith(".py"):
                compile(data, name, "exec")
            if name.endswith(".json"):
                json.loads(data)
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    result = gzip.compress(stream.getvalue(), mtime=0)
    if len(result) > 1024 * 1024:
        raise DeploymentError("bundle_upload_too_large")
    return result


def definition():
    value = json.loads(DEFINITION.read_text())
    if value.get("name") != "SDK external lifecycle conformance" or value.get("enabled") is not False:
        raise DeploymentError("definition_must_remain_inactive")
    if value.get("trigger") != {"type": "cron", "schedule": "0 0 1 1 *", "timezone": "UTC"}:
        raise DeploymentError("staging_schedule_required")
    return value


def identity(cloud):
    user = cloud.request("GET", "/api/v1/users/me")
    if user.get("id") != OWNER or user.get("org_id") != OWNER or user.get("git_user_name") != "enyst":
        raise DeploymentError("wrong_cloud_identity")


def owned(cloud, identifier):
    try:
        identifier = str(uuid.UUID(identifier))
    except ValueError:
        raise DeploymentError("invalid_automation_id") from None
    value = cloud.request("GET", API + "/" + identifier)
    if value.get("name") != definition()["name"] or value.get("user_id") != OWNER or value.get("org_id") != OWNER:
        raise DeploymentError("automation_identity_mismatch")
    return value


def brief(value):
    return {k: value[k] for k in ("id", "name", "enabled", "state", "trigger", "status", "sandbox_id", "created_at", "started_at", "completed_at") if k in value}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("dry-run", "create"):
        item = sub.add_parser(command)
        item.add_argument("--config", type=Path, default=ROOT / "examples/sdk-lifecycle-conformance.json")
    sub.add_parser("preflight")
    for command in ("dispatch", "status"):
        item = sub.add_parser(command)
        item.add_argument("--automation-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "dry-run":
        archive = package(args.config)
        print(json.dumps({"mode": "dry-run", "network_requests": 0, "definition": definition(),
                          "files": list(FILES), "bundle_tarball_sha256": hashlib.sha256(archive).hexdigest(),
                          "bundle_bytes": len(archive), "deployment": "not performed"}))
        return 0
    credential = os.environ.get("OPENHANDS_API_KEY", "")
    if not credential:
        raise DeploymentError("cloud_bearer_missing")
    cloud = Client(CLOUD, credential)
    identity(cloud)
    if args.command == "preflight":
        capabilities = cloud.request("GET", API + "/capabilities")
        print(json.dumps({"account": "enyst", "ready": capabilities.get("ready"),
                          "capabilities": {k: capabilities.get(k) for k in ("triggerKinds", "features")}}))
    elif args.command == "create":
        # Import trusted verifier validation, never candidate package/code.
        sys.path.insert(0, str(SOURCE))
        import main as verifier
        try:
            verifier.validate_config(json.loads(args.config.read_text()))
        except verifier.Blocked:
            raise DeploymentError("candidate_fixture_configuration_incomplete") from None
        inventory = cloud.request("GET", API + "?limit=100")
        if inventory.get("total", 0) > 100:
            raise DeploymentError("inventory_needs_pagination")
        if any(row.get("name") == definition()["name"] for row in inventory.get("automations", [])):
            raise DeploymentError("automation_already_exists")
        archive = package(args.config, credential)
        uploaded = cloud.request("POST", API + "/uploads?name=SDK-external-lifecycle-conformance", archive, "application/gzip")
        if uploaded.get("status") != "COMPLETED" or not str(uploaded.get("tarball_path", "")).startswith("oh-internal://uploads/"):
            raise DeploymentError("upload_incomplete")
        desired = dict(definition(), tarball_path=uploaded["tarball_path"])
        validation = cloud.request("POST", API + "/validate", {"endpoint": "/v1", "draft": desired})
        if validation.get("valid") is not True:
            raise DeploymentError("definition_rejected")
        created = cloud.request("POST", API, desired)
        observed = owned(cloud, created["id"])
        if observed.get("enabled") is not False:
            # Do not dispatch or silently consider registration safe.
            raise DeploymentError("inactive_registration_not_confirmed")
        print(json.dumps({"automation": brief(observed), "tarball_sha256": hashlib.sha256(archive).hexdigest(), "dispatched": False}))
    else:
        automation = owned(cloud, args.automation_id)
        if args.command == "dispatch":
            run = cloud.request("POST", API + "/" + automation["id"] + "/dispatch")
            print(json.dumps({"run": brief(run), "verdict": "pending; inspect retained verifier evidence"}))
        else:
            runs = cloud.request("GET", API + "/" + automation["id"] + "/runs?limit=10")
            print(json.dumps({"automation": brief(automation), "runs": [brief(r) for r in runs.get("runs", [])]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeploymentError as exc:
        print(json.dumps({"status": "blocked", "code": str(exc)}))
        raise SystemExit(2)
