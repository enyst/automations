#!/usr/bin/env python3
"""Manual Cloud definition export. GET-only; never runs git or an automation."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

HOST = "https://app.all-hands.dev"
API = "/api/automation/v1"
MAX_JSON = 8 * 1024 * 1024
MAX_ARCHIVE = 20 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_UNPACKED = 64 * 1024 * 1024
MAX_FILES = 2000
FIELDS = ("name", "model", "trigger", "setup_script_path", "entrypoint",
          "timeout", "keep_alive", "enabled", "prompt", "preset_metadata")
SECRET_PATTERNS = [
    rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
    rb"\bsk-(?:oh-|proj-|ant-)?[A-Za-z0-9_-]{20,}",
    rb"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}",
    rb"(?i)(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)\s*[=:]\s*[\"']([A-Za-z0-9_./+=-]{16,})[\"']",
    rb"https?://[^\s/@:]+:[^\s/@]+@",
    rb"(?i)[?&](?:token|api_key|access_token|signature|x-amz-signature|x-goog-signature)=[^&\s]+",
]
SECRET_KEYS = re.compile(r"(?i)(?:^|_)(?:api_key|access_token|refresh_token|password|client_secret|private_key)$")


class ExportError(Exception):
    """A closed, non-sensitive failure code."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def scan(data: bytes, credential: str = "") -> None:
    if credential and credential.encode() in data:
        raise ExportError("credential_content")
    if any(re.search(pattern, data) for pattern in SECRET_PATTERNS):
        raise ExportError("credential_like_content")


def scan_fields(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEYS.search(str(key)) and item not in (None, "", [], {}):
                raise ExportError("secret_metadata_field")
            scan_fields(item)
    elif isinstance(value, list):
        for item in value:
            scan_fields(item)


def identifier(value) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ExportError("invalid_identity") from None


def verify_identity(identity: dict, org_id: str, user_id: str | None = None) -> dict:
    if not isinstance(identity, dict) or identity.get("git_user_name") != "enyst":
        raise ExportError("wrong_account")
    actual = {"login": "enyst", "user_id": identifier(identity.get("id")),
              "org_id": identifier(identity.get("org_id"))}
    if actual["org_id"] != identifier(org_id):
        raise ExportError("wrong_org")
    if user_id is not None and actual["user_id"] != identifier(user_id):
        raise ExportError("wrong_user")
    return actual


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, credential: str):
        self.credential = credential
        # Redirects are refused, including same-origin redirects.
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, path: str, limit: int) -> bytes:
        if not (path == "/api/v1/users/me" or path.startswith(API)):
            raise ExportError("invalid_api_path")
        if "://" in path or "\r" in path or "\n" in path or path.startswith("//"):
            raise ExportError("invalid_api_path")
        request = urllib.request.Request(HOST + path, method="GET", headers={
            "Authorization": "Bearer " + self.credential,
            "Accept": "application/json, application/octet-stream"})
        try:
            with self.opener.open(request, timeout=60) as response:
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ExportError("response_too_large")
                return data
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise ExportError("http_" + str(code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ExportError("network_unavailable") from None

    def json(self, path: str):
        try:
            return json.loads(self.get(path, MAX_JSON))
        except (ValueError, UnicodeError):
            raise ExportError("invalid_response_json") from None

    def bundle(self, automation_id: str) -> bytes:
        return self.get(API + "/" + automation_id + "/tarball", MAX_ARCHIVE)


def list_all(client) -> list[dict]:
    rows, seen, total = [], set(), None
    for page in range(100):
        response = client.json(API + "?limit=100&offset=" + str(page * 100))
        batch = response.get("automations") if isinstance(response, dict) else None
        count = response.get("total") if isinstance(response, dict) else None
        if not isinstance(batch, list) or type(count) is not int or count < 0:
            raise ExportError("invalid_pagination")
        if total is None:
            total = count
        if total != count:
            raise ExportError("listing_changed")
        for row in batch:
            key = identifier(row.get("id"))
            if key in seen:
                raise ExportError("duplicate_page_entry")
            seen.add(key)
            rows.append(row)
        if len(rows) == total:
            return rows
        if not batch or len(rows) > total:
            raise ExportError("invalid_pagination")
    raise ExportError("pagination_limit")


def public_definition(raw: dict, identity: dict) -> tuple[dict, dict]:
    if not isinstance(raw, dict):
        raise ExportError("invalid_definition")
    binding = {"id": identifier(raw.get("id")), "user_id": identifier(raw.get("user_id")),
               "org_id": identifier(raw.get("org_id"))}
    if binding["user_id"] != identity["user_id"] or binding["org_id"] != identity["org_id"]:
        raise ExportError("definition_owner_changed")
    if not isinstance(raw.get("name"), str) or not isinstance(raw.get("trigger"), dict):
        raise ExportError("invalid_definition")
    if not isinstance(raw.get("entrypoint"), str) or type(raw.get("enabled")) is not bool:
        raise ExportError("invalid_definition")
    fields = {key: raw.get(key) for key in FIELDS}
    path = raw.get("tarball_path")
    if not isinstance(path, str) or not path:
        raise ExportError("invalid_bundle_pointer")
    if path.startswith("oh-internal://"):
        fields["tarball_source"] = {"type": "internal", "url": None}
    elif path.startswith("https://"):
        url = urllib.parse.urlsplit(path)
        if url.username or url.password:
            raise ExportError("credential_url")
        fields["tarball_source"] = {"type": "external", "url": path}
    else:
        raise ExportError("unsupported_bundle_pointer")
    scan_fields(fields)
    # Keep the observed timestamp as provenance; scheduler polling also updates it.
    binding.update({"tarball_path": path, "updated_at": raw.get("updated_at")})
    if raw.get("agent_profile_id") is not None:
        profile_id = raw["agent_profile_id"]
        if not isinstance(profile_id, str) or len(profile_id) > 1024:
            raise ExportError("invalid_profile_reference")
        binding["agent_profile_id"] = profile_id
    return fields, binding


def unpack(data: bytes, credential: str = "") -> tuple[dict[str, bytes], list[str]]:
    if len(data) > MAX_ARCHIVE:
        raise ExportError("archive_too_large")
    files, executables, seen, total = {}, [], set(), 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            for member in archive:
                name = member.name
                path = PurePosixPath(name)
                if (not name or name.startswith("/") or "\\" in name or
                        ".." in path.parts or any(ord(c) < 32 for c in name)):
                    raise ExportError("unsafe_archive_path")
                normalized = str(path)
                if normalized in ("", "."):
                    if member.isdir():
                        continue
                    raise ExportError("unsafe_archive_path")
                if normalized in seen:
                    raise ExportError("duplicate_archive_path")
                seen.add(normalized)
                if len(seen) > MAX_FILES * 2:
                    raise ExportError("archive_member_limit")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ExportError("archive_link_or_special")
                if member.size < 0 or member.size > MAX_FILE:
                    raise ExportError("archive_file_too_large")
                total += member.size
                if total > MAX_UNPACKED or len(files) >= MAX_FILES:
                    raise ExportError("archive_expansion_limit")
                # Nested archives/credential stores cannot receive meaningful text screening.
                if normalized.lower().endswith((".zip", ".whl", ".tar", ".tgz", ".tar.gz",
                                                 ".sqlite", ".sqlite3", ".db", ".p12", ".pfx")):
                    raise ExportError("opaque_nested_content")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ExportError("invalid_archive")
                content = stream.read(MAX_FILE + 1)
                if len(content) != member.size:
                    raise ExportError("invalid_archive")
                scan(content, credential)
                files[normalized] = content
                if member.mode & 0o111:
                    executables.append(normalized)
        if not files:
            raise ExportError("empty_archive")
        # Reject file/child collisions before touching the filesystem.
        for name in files:
            if any(str(parent) in files for parent in PurePosixPath(name).parents):
                raise ExportError("archive_path_collision")
        return files, sorted(executables)
    except (tarfile.TarError, EOFError, OSError, ValueError):
        raise ExportError("invalid_archive") from None


def recovery_bundle(recoveries: dict, automation_id: str, binding: dict):
    row = recoveries.get(automation_id)
    if not isinstance(row, dict):
        return None
    if row.get("tarball_path") != binding["tarball_path"]:
        raise ExportError("recovery_pointer_mismatch")
    path = Path(row.get("path", ""))
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
        raise ExportError("invalid_recovery_file")
    data = path.read_bytes()
    if digest(data) != row.get("sha256"):
        raise ExportError("recovery_hash_mismatch")
    provenance = row.get("provenance")
    if not isinstance(provenance, (str, dict)) or not provenance:
        raise ExportError("recovery_provenance_missing")
    # The archive path remains private to the operator; no machine paths are exported.
    return data, {"kind": "verified_local_archive", "sha256": digest(data),
                  "receipt": provenance}


def no_symlinks(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ExportError("output_symlink")


def write_atomic(path: Path, content: bytes) -> None:
    no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".export-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def definition_signature(raw: dict) -> str:
    """Compare exported behavior and origin bindings, excluding runtime bookkeeping."""
    if not isinstance(raw, dict):
        raise ExportError("invalid_definition")
    semantic = {key: raw.get(key) for key in FIELDS}
    for key in ("id", "user_id", "org_id"):
        semantic[key] = identifier(raw.get(key))
    semantic["tarball_path"] = raw.get("tarball_path")
    semantic["agent_profile_id"] = raw.get("agent_profile_id")
    return digest(encoded(semantic))


def listing_signature(rows):
    return sorted((identifier(row.get("id")), definition_signature(row)) for row in rows)


def export(client, output: Path, org_id: str, user_id: str | None = None,
           recoveries: dict | None = None) -> dict:
    identity = verify_identity(client.json("/api/v1/users/me"), org_id, user_id)
    credential = getattr(client, "credential", "")
    initial = list_all(client)
    plans, receipts = [], []
    for listed in initial:
        if identifier(listed.get("org_id")) != identity["org_id"]:
            raise ExportError("listing_cross_org")
        if identifier(listed.get("user_id")) != identity["user_id"]:
            continue
        automation_id = identifier(listed.get("id"))
        directory = "automation-" + automation_id
        fields = binding = None
        try:
            before_raw = client.json(API + "/" + automation_id)
            fields, binding = public_definition(before_raw, identity)
            if binding["id"] != automation_id:
                raise ExportError("definition_id_changed")
            if definition_signature(listed) != definition_signature(before_raw):
                raise ExportError("definition_changed_during_export")
            archive, provenance, files, executable = None, None, {}, []
            if fields["tarball_source"]["type"] == "internal":
                try:
                    archive = client.bundle(automation_id)
                    provenance = {"kind": "cloud_download", "sha256": digest(archive)}
                except ExportError as unavailable:
                    if not (unavailable.code.startswith("http_") or unavailable.code == "network_unavailable"):
                        raise
                    recovered = recovery_bundle(recoveries or {}, automation_id, binding)
                    if recovered is None:
                        raise
                    archive, provenance = recovered
                files, executable = unpack(archive, credential)
            after_raw = client.json(API + "/" + automation_id)
            _, after_binding = public_definition(after_raw, identity)
            if definition_signature(before_raw) != definition_signature(after_raw):
                raise ExportError("definition_changed_during_export")
            if executable:
                fields["tarball_executables"] = executable
            metadata = encoded(fields)
            scan(metadata, credential)
            receipt = {**binding, "directory": directory, "status": "complete",
                       "definition_sha256": digest(metadata),
                       "updated_at_after_download": after_binding["updated_at"],
                       "bundle": provenance or {"kind": "external_reference"},
                       "files": {name: digest(data) for name, data in sorted(files.items())}}
            scan_fields(receipt)
            scan(encoded(receipt), credential)
            plans.append((directory, metadata, files, executable))
        except ExportError as error:
            # Any potential credential stops the entire export before any files are written.
            if error.code in {"credential_content", "credential_like_content",
                              "secret_metadata_field", "credential_url"}:
                raise
            receipt = {"id": automation_id, "user_id": identity["user_id"],
                       "org_id": identity["org_id"], "directory": directory,
                       "status": "incomplete", "error": error.code}
            if fields is not None and binding is not None:
                safe_definition = encoded({"definition": fields, "binding": binding})
                scan(safe_definition, credential)
                plans.append((directory, None, {"definition.incomplete.json": safe_definition}, []))
        receipt["name"] = fields["name"] if fields else listed.get("name")
        receipt["enabled"] = fields["enabled"] if fields else listed.get("enabled")
        if binding and "agent_profile_id" in binding:
            receipt["agent_profile_id"] = binding["agent_profile_id"]
        receipts.append(receipt)
    if listing_signature(initial) != listing_signature(list_all(client)):
        raise ExportError("listing_changed")
    if identity != verify_identity(client.json("/api/v1/users/me"), org_id, identity["user_id"]):
        raise ExportError("identity_changed")
    no_symlinks(output)
    by_directory = {row["directory"]: row for row in receipts}
    for row in receipts:
        dest = output / row["directory"]
        no_symlinks(dest)
        row["preserved_existing"] = row["status"] != "complete" and (dest / "automation.yaml").is_file()
        scan_fields(row)
        scan(encoded(row), credential)
    listed_dirs = set(by_directory)
    stale = sorted(p.name for p in output.glob("automation-*")
                   if p.is_dir() and p.name not in listed_dirs) if output.exists() else []
    report = {"schema": 1, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "source": HOST, "identity": identity, "direction": "cloud_to_git_export_only",
              "complete": all(r["status"] == "complete" for r in receipts) and not stale,
              "listed": len(initial), "exported_owner_definitions": len(receipts),
              "skipped_other_owners": len(initial) - len(receipts),
              "not_in_current_listing_preserved": stale, "automations": receipts}
    scan(encoded(report), credential)
    for directory, metadata, files, executable in plans:
        dest = output / directory
        no_symlinks(dest)
        if metadata is None:
            # Good prior definition and bundle remain byte-for-byte unchanged.
            if dest.exists():
                continue
            for name, data in files.items():
                write_atomic(dest / name, data)
            continue
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".cloud-export-", dir=output) as temporary:
            stage = Path(temporary) / "new"
            stage.mkdir()
            write_atomic(stage / "automation.yaml", metadata)
            for name, data in files.items():
                target = stage / "tarball" / name
                write_atomic(target, data)
                target.chmod(0o755 if name in executable else 0o644)
            backup = Path(temporary) / "old"
            if dest.exists():
                os.replace(dest, backup)
            try:
                os.replace(stage, dest)
            except BaseException:
                if backup.exists():
                    os.replace(backup, dest)
                raise
    # Safe status is outside native metadata, including first-download failures.
    for row in receipts:
        write_atomic(output / row["directory"] / "export-status.json", encoded(row))
    write_atomic(output / "manifest.json", encoded(report))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-org-id", required=True)
    parser.add_argument("--expected-user-id")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "cloud-automations")
    parser.add_argument("--recovery-manifest", type=Path)
    args = parser.parse_args()
    try:
        recoveries = json.loads(args.recovery_manifest.read_text()) if args.recovery_manifest else {}
        if not isinstance(recoveries, dict):
            raise ExportError("invalid_recovery_manifest")
        result = subprocess.run(["security", "find-generic-password", "-s", "openhands",
                                 "-a", "OPENHANDS_API_KEY", "-w"],
                                capture_output=True, text=True, check=False)
        if result.returncode or not result.stdout.strip():
            raise ExportError("keychain_unavailable")
        client = Client(result.stdout.strip())
        report = export(client, args.output, args.expected_org_id, args.expected_user_id, recoveries)
        print(json.dumps({"complete": report["complete"],
                          "definitions": report["exported_owner_definitions"],
                          "incomplete": sum(r["status"] != "complete" for r in report["automations"])}))
        return 0 if report["complete"] else 2
    except ExportError as error:
        print(json.dumps({"complete": False, "error": error.code}))
        return 1
    except Exception:
        print(json.dumps({"complete": False, "error": "export_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
