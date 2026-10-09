#!/usr/bin/env python3
"""Read or materialize a Cloud export's hash-verified, commit-pinned source."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


class BundleError(Exception):
    pass


def relative_path(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(c) < 32 for c in value)):
        raise BundleError("invalid_bundle_path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or value == ".":
        raise BundleError("invalid_bundle_path")
    return value


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise BundleError("bundle_git_object_unavailable")
    return result.stdout


def source_files(root, source, hashes):
    """Read immutable Git blobs, never the current working tree or symlinks."""
    if (not isinstance(source, dict) or set(source) != {"commit", "path"}
            or not isinstance(source["commit"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", source["commit"])):
        raise BundleError("invalid_bundle_source")
    directory = relative_path(source["path"])
    if not isinstance(hashes, dict) or not hashes:
        raise BundleError("invalid_bundle_hashes")
    files = {}
    for name, expected in hashes.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise BundleError("invalid_bundle_hashes")
        path = directory + "/" + relative_path(name)
        # Exact literal path lookup also checks file type before reading a blob.
        entry = git(root, "ls-tree", "-z", source["commit"], "--", ":(literal)" + path)
        if not entry:
            raise BundleError("bundle_file_missing")
        metadata, actual = entry.rstrip(b"\0").split(b"\t", 1)
        mode, kind, blob = metadata.split()
        if actual.decode() != path or kind != b"blob" or mode not in (b"100644", b"100755"):
            raise BundleError("bundle_file_not_regular")
        content = git(root, "cat-file", "blob", blob.decode())
        if hashlib.sha256(content).hexdigest() != expected:
            raise BundleError("bundle_hash_mismatch")
        files[name] = content
    return files


def find_source(root, files, directory, previous=None):
    """Reuse committed maintained source first; otherwise preserve an old snapshot."""
    if not files:
        return None
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    try:
        commit = git(root, "rev-parse", "HEAD").decode().strip()
    except BundleError:
        return None  # Standalone exports keep the original inline layout.
    # A PR may be squash-merged and its branch deleted. Only pin HEAD when a
    # retained tag or main's history keeps the objects available to fresh clones.
    retained = git(root, "for-each-ref", "--contains=" + commit, "--format=%(refname)",
                   "refs/tags", "refs/remotes/origin/main")
    if not retained:
        try:
            commit = git(root, "rev-parse", "refs/remotes/origin/main").decode().strip()
        except BundleError:
            commit = None
    try:
        entries = git(root, "ls-tree", "-z", commit + ":sources").split(b"\0") if commit else []
    except BundleError:
        entries = []
    paths = ["sources/" + entry.split(b"\t", 1)[1].decode()
             for entry in entries if entry.startswith(b"040000 tree ")]
    candidates = [{"commit": commit, "path": path} for path in paths]
    if previous:
        candidates.append(previous)
    if commit and directory:
        candidates.append({"commit": commit, "path": directory + "/tarball"})
    for source in candidates:
        try:
            source_files(root, source, hashes)
        except BundleError:
            continue
        return source
    return None  # A changed, uncommitted Cloud bundle must still be stored in full.


def read_export(root, directory):
    receipt = json.loads((directory / "export-status.json").read_text())
    if receipt.get("status") != "complete" and receipt.get("preserved_existing"):
        receipt = receipt.get("last_complete", {})
    if receipt.get("status") != "complete":
        raise BundleError("export_incomplete")
    if hashlib.sha256((directory / "automation.yaml").read_bytes()).hexdigest() != receipt["definition_sha256"]:
        raise BundleError("definition_hash_mismatch")
    hashes = receipt["files"]
    if "source" in receipt:
        return source_files(root, receipt["source"], hashes)
    files = {}
    for name, expected in hashes.items():
        path = directory / "tarball" / relative_path(name)
        if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
            raise BundleError("bundle_file_not_regular")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise BundleError("bundle_hash_mismatch")
        files[name] = data
    return files


def materialize(root, directory, output):
    # Verification completes before creating any output. Fresh destinations only.
    files = read_export(root, directory)
    metadata = json.loads((directory / "automation.yaml").read_text())
    executables = metadata.get("tarball_executables", [])
    if any(part.is_symlink() for part in (output, *output.parents)):
        raise BundleError("output_symlink")
    output.mkdir(parents=True, exist_ok=False)
    (output / "automation.yaml").write_bytes((directory / "automation.yaml").read_bytes())
    for name, data in files.items():
        path = output / "tarball" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if name in executables else 0o644)
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="Cloud automation export directory")
    parser.add_argument("--output", type=Path, help="Create a native-layout copy in a new directory")
    args = parser.parse_args()
    count = (materialize(ROOT, args.export, args.output) if args.output
             else len(read_export(ROOT, args.export)))
    print(json.dumps({"verified_files": count, "materialized": args.output is not None}))


if __name__ == "__main__":
    try:
        main()
    except (BundleError, OSError, ValueError, KeyError) as error:
        code = str(error) if isinstance(error, BundleError) else type(error).__name__
        print(json.dumps({"error": code}))
        raise SystemExit(1)
