"""Read public, exact-commit Git objects without checking out repository code.

Only the controller calls ``fetch``. Agent tools expose ``read_file`` and
``search`` and additionally enforce their own frozen set of permitted commits.
No repository command, hook, diff driver, credential helper, or shell is run.
"""

from __future__ import annotations

import os
import re
import selectors
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any

MAX_BYTES = 1024 * 1024
MAX_BLOB_BYTES = 8 * 1024 * 1024
ALLOWED_REPOSITORIES = frozenset(
    {
        "openhands/openhands",
        "openhands/software-agent-sdk",
        "openhands/automation",
        "openhands/extensions",
    }
)
_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_GIT = "/usr/bin/git"


class SourceError(Exception):
    """Base class for explicit, nontruncating source-access failures."""


class SourceValidationError(SourceError, ValueError):
    """Invalid repository, commit, path, or read range."""


class SourceMissingError(SourceValidationError):
    """A valid source path does not exist at an admitted commit."""


class SourceBudgetExceeded(SourceError):
    """Source evidence exceeds its explicit blob or returned-context budget."""


class SourceCommandError(SourceError):
    """A fixed Git command failed or exceeded its time budget."""


def _sha(value: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise SourceValidationError("A full 40-character commit SHA is required")
    return value.lower()


def _path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise SourceValidationError(
            "Expected a relative repository file path without traversal"
        )
    return value


def _text(value: bytes) -> str:
    if b"\x00" in value:
        raise SourceValidationError("Binary file content is not a text source file")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceValidationError("Source file is not UTF-8 text") from exc


class SourceRepo:
    """A fresh bare store for one allowlisted public repository.

    The directory must be absent or empty. Objects are admitted only after an
    exact SHA fetch and commit-type verification. There is never a checkout.
    """

    def __init__(self, repository: str, directory: str | Path):
        if (
            not isinstance(repository, str)
            or repository.lower() not in ALLOWED_REPOSITORIES
        ):
            raise SourceValidationError(
                "Repository is outside the allowed public scope"
            )
        self.repository = repository.lower()
        requested = Path(directory)
        if requested.is_symlink():
            raise SourceValidationError("Source directory must not be a symlink")
        requested.mkdir(parents=True, exist_ok=True)
        self.directory = requested.resolve()
        if any(self.directory.iterdir()):
            raise SourceValidationError("Source directory must be fresh and empty")
        self._allowed_commits: set[str] = set()
        self._git(["init", "--bare", "."])

    @property
    def allowed_commits(self) -> frozenset[str]:
        return frozenset(self._allowed_commits)

    def _environment(self) -> dict[str, str]:
        # Construct a minimal environment, rather than trying to enumerate all
        # names that might carry GH, LLM, Cloud, callback, or KV credentials.
        return {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.directory),
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/usr/bin/false",
            "SSH_ASKPASS": "/usr/bin/false",
            "GIT_LFS_SKIP_SMUDGE": "1",
        }

    def _git(self, arguments: list[str], *, allow_no_match: bool = False) -> bytes:
        # Only this fixed object-read command can materialize a larger buffer
        # internally for pagination. There is no caller-selected output limit;
        # diffs, searches, manifests and all model-visible results stay at 1 MiB.
        blob_read = (
            len(arguments) == 3
            and arguments[:2] == ["cat-file", "blob"]
            and isinstance(arguments[2], str)
            and _SHA.fullmatch(arguments[2]) is not None
        )
        output_limit = MAX_BLOB_BYTES if blob_read else MAX_BYTES
        command = [
            _GIT,
            "--no-pager",
            "-c",
            "credential.helper=",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.quotePath=false",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "http.followRedirects=false",
            *arguments,
        ]
        process = subprocess.Popen(
            command,
            cwd=self.directory,
            env=self._environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        output = bytearray()
        errors = bytearray()
        deadline = time.monotonic() + 120
        assert process.stdout is not None and process.stderr is not None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, output)
                selector.register(process.stderr, selectors.EVENT_READ, errors)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SourceCommandError(
                            "Source Git operation exceeded 120 seconds"
                        )
                    for key, _ in selector.select(min(remaining, 1)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        key.data.extend(chunk)
                        if (
                            len(output) > output_limit
                            or len(errors) > MAX_BYTES
                            or (not blob_read and len(output) + len(errors) > MAX_BYTES)
                        ):
                            raise SourceBudgetExceeded(
                                f"Complete Git output exceeds its {output_limit // MAX_BYTES} MiB "
                                "budget; evidence was not truncated"
                            )
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            if code != 0 and not (allow_no_match and code == 1):
                # Do not reflect arbitrary repository content or process stderr
                # into model-visible errors. The fixed command category is enough.
                raise SourceCommandError(
                    f"Source Git operation {arguments[0]!r} failed (exit {code})"
                )
            return bytes(output)
        except subprocess.TimeoutExpired as exc:
            raise SourceCommandError(
                "Source Git operation exceeded 120 seconds"
            ) from exc
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            process.stderr.close()

    def _fetch_from_public(self, sha: str) -> None:
        self._git(
            [
                "fetch",
                "--quiet",
                "--no-tags",
                "--no-recurse-submodules",
                "--no-write-fetch-head",
                "--depth=1",
                f"https://github.com/{self.repository}.git",
                sha,
            ]
        )

    def fetch(self, *commits: str) -> frozenset[str]:
        """Fetch and admit exact public commits; no branch/ref interpolation."""
        requested = [_sha(commit) for commit in commits]
        for sha in requested:
            if sha in self._allowed_commits:
                continue
            self._fetch_from_public(sha)
            if self._git(["cat-file", "-t", sha]).strip() != b"commit":
                raise SourceValidationError("Fetched object is not a commit")
            self._allowed_commits.add(sha)
        return self.allowed_commits

    def _admitted(self, sha: str) -> str:
        sha = _sha(sha)
        if sha not in self._allowed_commits:
            raise SourceValidationError(
                "Commit has not been explicitly fetched and admitted"
            )
        return sha

    def _entry(self, sha: str, path: str) -> dict[str, str]:
        # Literal pathspec prevents filenames containing glob magic from
        # selecting another path. A tree/blob query never follows symlinks.
        raw = self._git(["ls-tree", "-z", sha, "--", f":(literal){path}"])
        entries = [entry for entry in raw.split(b"\x00") if entry]
        if not entries:
            raise SourceMissingError("File does not exist at the requested commit")
        if len(entries) != 1:
            raise SourceValidationError(
                "Requested path did not resolve to one exact object"
            )
        metadata, encoded_path = entries[0].split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        if encoded_path.decode("utf-8") != path:
            raise SourceValidationError(
                "Requested path did not resolve to one exact object"
            )
        return {"mode": mode, "kind": kind, "oid": oid}

    def _blob(self, sha: str, path: str) -> tuple[dict[str, str], str]:
        entry = self._entry(sha, path)
        if entry["kind"] != "blob":
            raise SourceValidationError("Requested path is not a file blob")
        size = int(self._git(["cat-file", "-s", entry["oid"]]).strip())
        if size > MAX_BLOB_BYTES:
            raise SourceBudgetExceeded(
                "Complete source file exceeds 8 MiB; evidence was not truncated"
            )
        return entry, _text(self._git(["cat-file", "blob", entry["oid"]]))

    def read_file(
        self, sha: str, path: str, start_line: int = 1, end_line: int | None = None
    ) -> dict[str, Any]:
        """Read numbered inclusive lines; default is the complete UTF-8 blob.

        A symlink yields its stored link target as text and is never followed.
        Text blobs up to 8 MiB can be paged, but each returned result remains
        limited to 1 MiB. Larger/binary files fail even for a requested slice.
        """
        sha, path = self._admitted(sha), _path(path)
        if (
            isinstance(start_line, bool)
            or not isinstance(start_line, int)
            or start_line < 1
        ):
            raise SourceValidationError("start_line must be a positive integer")
        if end_line is not None and (
            isinstance(end_line, bool)
            or not isinstance(end_line, int)
            or end_line < start_line
        ):
            raise SourceValidationError("end_line must be at least start_line")
        entry, text = self._blob(sha, path)
        parts = text.split("\n")
        lines = [line + "\n" for line in parts[:-1]]
        if parts[-1]:
            lines.append(parts[-1])
        if start_line > len(lines) + 1:
            raise SourceValidationError("start_line is past the end of the file")
        actual_end = min(end_line or len(lines), len(lines))
        return self._budget(
            {
                "sha": sha,
                "path": path,
                "mode": entry["mode"],
                "content": "".join(lines[start_line - 1 : actual_end]),
                "start_line": start_line,
                "end_line": actual_end,
                "total_lines": len(lines),
                "next_start_line": actual_end + 1 if actual_end < len(lines) else None,
            }
        )

    def search(
        self, sha: str, literalpattern: str, offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        """Case-sensitive literal search; stable pagination over all matches."""
        sha = self._admitted(sha)
        if (
            not isinstance(literalpattern, str)
            or not literalpattern
            or any(char in literalpattern for char in "\x00\n\r")
        ):
            raise SourceValidationError(
                "Search requires a nonempty single-line literal"
            )
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise SourceValidationError("Search offset must be a nonnegative integer")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise SourceValidationError("Search limit must be from 1 to 100")
        raw = self._git(
            ["grep", "-n", "-z", "-F", "-I", "-e", literalpattern, sha, "--"],
            allow_no_match=True,
        )
        matches: list[dict[str, Any]] = []
        while raw:
            filename, raw = raw.split(b"\x00", 1)
            line, raw = raw.split(b"\x00", 1)
            content, _, raw = raw.partition(b"\n")
            path = filename.decode("utf-8").removeprefix(sha + ":")
            matches.append({"path": path, "line": int(line), "text": _text(content)})
        stop = offset + limit
        return self._budget(
            {
                "sha": sha,
                "pattern": literalpattern,
                "matches": matches[offset:stop],
                "offset": offset,
                "total_matches": len(matches),
                "next_offset": stop if stop < len(matches) else None,
            }
        )

    def _manifest(self, base: str, head: str) -> list[dict[str, Any]]:
        raw = self._git(
            [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--name-status",
                "-z",
                base,
                head,
                "--",
            ]
        )
        tokens = raw.split(b"\x00")
        files: list[dict[str, Any]] = []
        for index in range(0, len(tokens) - 1, 2):
            status, path = (
                tokens[index].decode("ascii"),
                tokens[index + 1].decode("utf-8"),
            )
            _path(path)
            before = self._entry(base, path) if status != "A" else None
            after = self._entry(head, path) if status != "D" else None
            files.append(
                {
                    "status": status,
                    "path": path,
                    "base_path": path if before else None,
                    "head_path": path if after else None,
                    "base_mode": before["mode"] if before else None,
                    "head_mode": after["mode"] if after else None,
                }
            )
        return files

    def diff_manifest(self, base: str, head: str) -> dict[str, Any]:
        """Complete changed-path manifest for admitted commits, without a patch.

        This is an explicit alternative for historical patches too large to
        preload. The manifest itself is still bounded and never truncated.
        """
        base, head = self._admitted(base), self._admitted(head)
        return self._budget({"base": base, "head": head, "files": self._manifest(base, head)})

    def diff(self, base: str, head: str) -> dict[str, Any]:
        """Complete textual Git patch plus manifest; no implicit truncation.

        Binary deltas are explicitly represented by Git's binary-difference
        notice; changed_files_context rejects unsupported binary blob content.
        """
        base, head = self._admitted(base), self._admitted(head)
        patch = _text(
            self._git(
                [
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-renames",
                    "--full-index",
                    "--no-color",
                    base,
                    head,
                    "--",
                ]
            )
        )
        return self._budget(
            {
                "base": base,
                "head": head,
                "patch": patch,
                "files": self._manifest(base, head),
            }
        )

    def changed_files_context(self, base: str, head: str) -> dict[str, Any]:
        """Complete before/after content for every changed path, or fail.

        Symlink contents are literal link text. Submodules are explicit gitlink
        object IDs; this helper never fetches or enters their repositories.
        """
        base, head = self._admitted(base), self._admitted(head)
        files = self._manifest(base, head)
        for item in files:
            for label, sha in (("before", base), ("after", head)):
                path = item["base_path" if label == "before" else "head_path"]
                if path is None:
                    item[label] = None
                    continue
                entry = self._entry(sha, path)
                if entry["mode"] == "160000":
                    item[label] = {"kind": "gitlink", "sha": entry["oid"]}
                else:
                    _, content = self._blob(sha, path)
                    item[label] = {
                        "kind": "symlink" if entry["mode"] == "120000" else "file",
                        "content": content,
                    }
            self._budget({"base": base, "head": head, "files": files})
        return self._budget({"base": base, "head": head, "files": files})

    @staticmethod
    def _budget(value: dict[str, Any]) -> dict[str, Any]:
        import json

        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
            raise SourceBudgetExceeded(
                "Complete source context exceeds 1 MiB; evidence was not truncated"
            )
        return value
