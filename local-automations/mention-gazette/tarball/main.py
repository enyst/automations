#!/usr/bin/env python3
import json
import os
import shlex
import subprocess
import tempfile
import urllib.request
from pathlib import Path

GENERATOR = Path.home() / ".smolpaws/tools/gazette/build_gazette.py"
REPO = Path.home() / "repos/enyst.github.io"
OUTPUT_REL = "arch/mention-gazette.html"
OUTPUT = REPO / OUTPUT_REL
REMOTE_HTTPS = "https://github.com/enyst/enyst.github.io.git"


def fire_callback(status="COMPLETED", error=None):
    """Signal run completion. MUST be called on every exit path — success AND error."""
    url = os.environ.get("AUTOMATION_CALLBACK_URL", "")
    if not url:
        return
    body = {"status": status, "run_id": os.environ.get("AUTOMATION_RUN_ID", "")}
    if error:
        body["error"] = error
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('AUTOMATION_CALLBACK_API_KEY', '')}",
        }))
    except Exception as e:
        print(f"Callback error: {e}")


def run(args, *, env=None, check=True):
    printable = " ".join(shlex.quote(str(a)) for a in args)
    print(f"$ {printable}")
    proc = subprocess.run(
        [str(a) for a in args],
        cwd=str(REPO) if REPO.exists() else None,
        env=env,
        text=True,
        capture_output=True,
    )
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="")
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {printable}")
    return proc


def git(*args, env=None, check=True):
    return run(["git", "-C", str(REPO), *args], env=env, check=check)


def get_github_pat():
    proc = subprocess.run(
        ["security", "find-generic-password", "-l", "GitHub PAT for enyst", "-w"],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("could not read GitHub PAT for enyst from macOS keychain")
    token = proc.stdout.strip()
    if not token:
        raise RuntimeError("GitHub PAT for enyst in macOS keychain was empty")
    return token


def ensure_clean_enough():
    status = git("status", "--porcelain", check=True).stdout.splitlines()
    allowed = {" M " + OUTPUT_REL, "M  " + OUTPUT_REL, "MM " + OUTPUT_REL, "?? " + OUTPUT_REL}
    unexpected = [line for line in status if line not in allowed]
    if unexpected:
        raise RuntimeError("refusing to run with unrelated local changes:\n" + "\n".join(unexpected))


def push_with_fallback(env):
    ssh_push = git("push", "origin", "main", env=env, check=False)
    if ssh_push.returncode == 0:
        return

    with tempfile.TemporaryDirectory() as td:
        askpass = Path(td) / "askpass.sh"
        askpass.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' x-access-token ;;\n"
            "  *) printf '%s\\n' \"$GH_TOKEN\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        askpass.chmod(0o700)
        fallback_env = {
            **env,
            "GIT_ASKPASS": str(askpass),
            "GIT_TERMINAL_PROMPT": "0",
        }
        git("push", REMOTE_HTTPS, "HEAD:main", env=fallback_env, check=True)


def main():
    if not GENERATOR.exists():
        raise RuntimeError(f"generator missing: {GENERATOR}")
    if not REPO.exists():
        raise RuntimeError(f"publishing repo missing: {REPO}")

    token = get_github_pat()
    env = {**os.environ, "GH_TOKEN": token, "GITHUB_TOKEN": token}

    ensure_clean_enough()
    git("pull", "--ff-only", "origin", "main", env=env, check=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    run(["python3", str(GENERATOR), str(OUTPUT)], env=env, check=True)

    diff = git("diff", "--quiet", "--", OUTPUT_REL, env=env, check=False)
    if diff.returncode == 0:
        print("Mention Gazette unchanged; nothing to commit.")
        return
    if diff.returncode != 1:
        raise RuntimeError("git diff failed")

    git("add", OUTPUT_REL, env=env, check=True)
    git(
        "-c", "user.name=Engel Nyst",
        "-c", "user.email=engel.nyst@gmail.com",
        "commit",
        "-m", "Update Mention Gazette",
        "-m", "Co-authored-by: smolpaws <engel@enyst.org>\nCo-authored-by: openhands <openhands@all-hands.dev>",
        env=env,
        check=True,
    )
    push_with_fallback(env)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Mention Gazette automation failed: {e}")
        fire_callback("FAILED", str(e))
        raise
    else:
        fire_callback("COMPLETED")
