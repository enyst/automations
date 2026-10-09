#!/usr/bin/env python3
"""Stage/update the Cloud Gazette, paused. Explicit dispatch and cutover are separate."""
import hashlib
import json
from pathlib import Path
import sys

from deploy_jev import API, CLOUD, Client, DeploymentError, bundle, identity, keychain, secret_names

ROOT = Path(__file__).resolve().parents[1]
SECRET = "REMOTE_GH"
STAGING = {"type": "cron", "schedule": "0 0 1 1 *", "timezone": "UTC"}


def main():
    cloud = Client(CLOUD, keychain("OPENHANDS_API_KEY"))
    identity(cloud)
    listing = cloud.request("GET", API + "?limit=100")
    if listing.get("total", 0) > 100:
        raise DeploymentError("pagination_required")
    matches = [a for a in listing["automations"] if a["name"] == "Mention Gazette"]
    if len(matches) > 1:
        raise DeploymentError("multiple_gazette_definitions")
    if SECRET not in secret_names(cloud):
        raise DeploymentError("remote_gh_secret_missing")
    archive, files = bundle(ROOT / "sources/mention-gazette", (cloud.credential,))
    if set(files) != {"main.py", "render.py", "README.md"}:
        raise DeploymentError("unexpected_runtime_files")
    upload = cloud.request("POST", API + "/uploads?name=Mention-Gazette", archive, "application/gzip")
    if upload.get("status") != "COMPLETED": raise DeploymentError("upload_failed")
    desired = json.loads((ROOT / "definitions/mention-gazette.json").read_text())
    desired.update(trigger=STAGING, tarball_path=upload["tarball_path"])
    valid = cloud.request("POST", API + "/validate", {"endpoint": "/v1", "draft": desired})
    if valid.get("valid") is not True: raise DeploymentError("definition_invalid")
    if matches:
        if matches[0]["enabled"]: raise DeploymentError("pause_existing_gazette_before_update")
        result = cloud.request("PATCH", API + "/" + matches[0]["id"], desired)
    else:
        result = cloud.request("POST", API, desired)
    identifier = result["id"]
    cloud.request("PATCH", API + "/" + identifier, {"enabled": False})
    readback = cloud.request("GET", API + "/" + identifier)
    if readback["enabled"] or readback["tarball_path"] != upload["tarball_path"]:
        raise DeploymentError("readback_mismatch")
    print(json.dumps({"id": identifier, "enabled": False, "sha256": hashlib.sha256(archive).hexdigest(), "files": files}))


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, DeploymentError) else type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
