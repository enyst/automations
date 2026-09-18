"""QA Changes — Auto-Test PR description (software-agent-sdk).

Event-triggered automation that runs a pre-QA gate in-process and, only when
every gate passes, starts an agent conversation to perform QA and append an
"## Auto-Test" section to the PR description.

Design:
  * The three gates (not-draft, no existing "Auto-Test" section, author has
    >3 commits merged to main) are evaluated HERE, deterministically, before
    any conversation is created. A gate failure fires the completion callback
    and exits cleanly — no LLM, no sandbox conversation, no cost.
  * When gates pass, the PR number + metadata are injected into the prompt so
    the agent goes straight to QA instead of rediscovering the PR.

The GitHub payload comes from AUTOMATION_EVENT_PAYLOAD (shape: a JSON object
with an "event" key holding the webhook body; "pull_request" inside it carries
number, draft, body, user.login, base.repo.full_name, etc.).
"""
import json
import os
import sys
from datetime import datetime, timezone

from openhands.sdk import Conversation, RemoteConversation
from openhands.tools.preset import TaskOutcome
from finish_tool_hook import finish_tool_required_hook_config

REPO = "OpenHands/software-agent-sdk"
MIN_MERGED_COMMITS = 3  # "more than 3 commits" -> require strictly > 3

# Detect execution mode based on AGENT_SERVER_URL presence (mirrors preset).
agent_server_url = os.environ.get("AGENT_SERVER_URL", "").rstrip("/")
IS_LOCAL_MODE = bool(agent_server_url)

api_key = os.environ.get("OPENHANDS_API_KEY", "")
api_url = os.environ.get("OPENHANDS_CLOUD_API_URL", "").rstrip("/")
session_key = os.environ.get("OH_SESSION_API_KEYS_0") or os.environ.get(
    "SESSION_API_KEY", ""
)
model_profile = os.environ.get("AUTOMATION_MODEL") or None


def _load_event() -> dict | None:
    raw = os.environ.get("AUTOMATION_EVENT_PAYLOAD")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _extract_pr(event_context: dict) -> dict | None:
    """Pull the pull_request object out of the event context."""
    if not isinstance(event_context, dict):
        return None
    event = event_context.get("event")
    if not isinstance(event, dict):
        return None
    pr = event.get("pull_request")
    return pr if isinstance(pr, dict) else None


def _repo_full_name(pr: dict, event_context: dict) -> str:
    base = pr.get("base") or {}
    repo = base.get("repo") or {}
    full_name = repo.get("full_name")
    if full_name:
        return full_name
    event = event_context.get("event") if isinstance(event_context, dict) else {}
    repo = event.get("repository") or {}
    return repo.get("full_name") or REPO


def _count_author_commits(full_name: str, author: str) -> int | None:
    """Count commits by `author` merged into main. None on API error (fail closed)."""
    import urllib.request

    token = (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("github_token")
        or os.environ.get("REMOTE_GH")
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "qa-auto-test-gate",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    total = 0
    url = (
        f"https://api.github.com/repos/{full_name}/commits"
        f"?sha=main&author={author}&per_page=100"
    )
    while url:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
                link = r.headers.get("Link", "")
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        total += len(data)
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                start = part.find("<")
                end = part.find(">")
                if start != -1 and end != -1:
                    next_url = part[start + 1 : end]
        url = next_url
        if not data:
            break
    return total


def _run_gates(pr: dict, event_context: dict) -> list[str]:
    failures: list[str] = []

    # Cheap, local gates first — short-circuit before any network call.
    if pr.get("draft") is not False:
        failures.append("PR is a draft")
        return failures

    body = (pr.get("body") or "").lower()
    if "auto-test" in body or "auto test" in body:
        failures.append("PR description already contains an Auto-Test section")
        return failures

    author = (pr.get("user") or {}).get("login")
    full_name = _repo_full_name(pr, event_context)
    if not author:
        failures.append("could not determine PR author")
    else:
        commits = _count_author_commits(full_name, author)
        if commits is None:
            failures.append(f"could not verify author {author!r} commit count to main")
        elif commits <= MIN_MERGED_COMMITS:
            failures.append(
                f"author {author!r} has only {commits} merged commits to main "
                f"(need > {MIN_MERGED_COMMITS})"
            )

    return failures


def _fire_callback(status: str = "COMPLETED", error: str | None = None) -> None:
    import urllib.request

    url = os.environ.get("AUTOMATION_CALLBACK_URL", "")
    if not url:
        return
    body = {"status": status, "run_id": os.environ.get("AUTOMATION_RUN_ID", "")}
    if error:
        body["error"] = error
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {os.environ.get('AUTOMATION_CALLBACK_API_KEY', '')}",
                },
            ),
            timeout=10,
        )
    except Exception:
        pass


def _make_phase_reporter():
    phase_url = os.environ.get("AUTOMATION_PHASE_URL", "")
    phase_token = (
        os.environ.get("AUTOMATION_CALLBACK_API_KEY")
        or os.environ.get("OPENHANDS_API_KEY")
        or ""
    )

    def report_phase(message: str) -> None:
        if not phase_url or not phase_token or not message:
            return
        try:
            import httpx

            httpx.post(
                phase_url,
                json={"phase": message[:200]},
                headers={"Authorization": f"Bearer {phase_token}"},
                timeout=5.0,
            )
        except Exception:
            pass

    return report_phase


def _build_conversation_title(pr: dict, event_context: dict) -> str | None:
    name = (
        event_context.get("automation_name")
        if isinstance(event_context, dict)
        else None
    )
    if not isinstance(name, str) or not name.strip():
        return None
    name = " ".join(name.split())
    number = pr.get("number")
    full_name = _repo_full_name(pr, event_context)
    if isinstance(number, int) and not isinstance(number, bool) and full_name:
        context = f"{full_name.rsplit('/', 1)[-1]}#{number}"
    else:
        context = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"{name} — {context}"[:200]


def _build_prompt(pr: dict, event_context: dict) -> str:
    number = pr.get("number")
    title = pr.get("title")
    author = (pr.get("user") or {}).get("login")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    full_name = _repo_full_name(pr, event_context)

    return f"""## Target pull request

Repository: {full_name}
PR number: #{number}
Title: {title}
Author: {author}
Head ref: {head.get('ref')}
Base ref: {base.get('ref')}

The gate checks (not a draft, no existing Auto-Test section, author has >3
commits merged to main) have already passed. Proceed directly with QA.

## Event payload (pull_request)

```json
{json.dumps(pr, indent=2)}
```

## Task

Perform QA validation of PR #{number} in {full_name} using the /qa-changes
skill methodology:

- Phase 1: Understand the change. Read the PR diff, title, and description.
  Identify the goal of the PR. Classify every changed file. Form a hypothesis
  about what the PR should achieve.
- Phase 2: Set up the environment. Read the repo's bootstrap instructions,
  install dependencies, and build if needed. Note CI status but do not re-run
  tests.
- Phase 3: Exercise the changed behavior. Actually run the software the way a
  real user would — start servers, run CLI commands, make HTTP requests, open
  browsers. Do NOT run the test suite, linters, or code analysis. For bug
  fixes, reproduce the bug before and after the fix. Show before/after evidence.
- Phase 4: Report results.

IMPORTANT — how to post your report:
Do NOT post a new comment, review, or inline review comment. Instead, EDIT the
PR description (the pull request body) to append your QA report at the very end,
after a horizontal rule (---), under a new section titled Auto-Test. Use the
GitHub API (PATCH /repos/{full_name}/pulls/{number} with a body field) to update
the PR description. Append the following structure to the existing PR body:

---

## Auto-Test

{{Your full QA report following the /qa-changes skill report format: verdict,
summary, "Does this PR achieve its stated goal?" section, status table,
collapsible evidence in details blocks, and issues found.}}

Do not make any other changes to the PR description — only append the Auto-Test
section at the end. Do not post any comments or reviews.
"""


def _run_conversation(pr: dict, event_context: dict) -> None:
    from openhands.sdk.workspace.remote.base import RemoteWorkspace
    from openhands.tools.preset.default import get_default_agent
    from openhands.workspace import OpenHandsCloudWorkspace

    report_phase = _make_phase_reporter()
    workspace_base = os.path.expanduser(os.environ.get("WORKSPACE_BASE", "/workspace"))

    if IS_LOCAL_MODE:
        workspace_ctx = RemoteWorkspace(
            host=agent_server_url,
            api_key=session_key if session_key else None,
            working_dir=workspace_base,
        )
    else:
        workspace_ctx = OpenHandsCloudWorkspace(
            local_agent_server_mode=True,
            cloud_api_url=api_url,
            cloud_api_key=api_key,
            keep_alive=True,
        )

    with workspace_ctx as workspace:
        report_phase("Setting up workspace")

        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        repos_config_file = os.path.join(SCRIPT_DIR, "repos_config.json")
        clone_result = None
        repo_dirs = []
        if os.path.exists(repos_config_file):
            with open(repos_config_file) as f:
                repos_config = json.load(f)
            if repos_config:
                report_phase("Cloning repositories")
                clone_result = workspace.clone_repos(repos_config)
                repo_dirs = [m.local_path for m in clone_result.repo_mappings.values()]

        report_phase("Loading skills")
        loaded_skills, agent_context = workspace.load_skills_from_agent_server(
            project_dirs=repo_dirs if repo_dirs else None
        )

        report_phase("Configuring agent")
        try:
            llm = workspace.get_llm(profile_name=model_profile)
        except FileNotFoundError:
            if not model_profile:
                raise
            llm = workspace.get_llm()

        agent = get_default_agent(
            llm=llm,
            cli_mode=True,
            finish_tool_response_schema=TaskOutcome,
        )
        if agent_context:
            agent = agent.model_copy(update={"agent_context": agent_context})

        secrets = {}
        try:
            secrets = workspace.get_secrets()
        except Exception:
            pass

        prompt = _build_prompt(pr, event_context)

        report_phase("Starting QA conversation")
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            hook_config=finish_tool_required_hook_config(SCRIPT_DIR),
            delete_on_close=False,
        )
        assert isinstance(conversation, RemoteConversation)

        title = _build_conversation_title(pr, event_context)
        if title:
            try:
                workspace.client.patch(
                    f"/api/conversations/{conversation.id}", json={"title": title}
                )
            except Exception:
                pass

        if secrets:
            conversation.update_secrets(secrets)

        conversation.send_message(prompt)
        conversation.run()
        conversation.close()


def main() -> int:
    event_context = _load_event()
    pr = _extract_pr(event_context) if event_context else None

    if not pr:
        _fire_callback("FAILED", "No pull_request found in AUTOMATION_EVENT_PAYLOAD")
        print("FAIL: no pull_request in event payload; nothing done")
        return 1

    number = pr.get("number")
    title = pr.get("title")
    author = (pr.get("user") or {}).get("login")

    failures = _run_gates(pr, event_context)

    if failures:
        print("=== GATE CHECK FAILED ===")
        for f in failures:
            print(f"  - {f}")
        print(
            "Stopping before creating a conversation: no QA performed, "
            "no PR changes, no comments/reviews posted."
        )
        _fire_callback("COMPLETED", "; ".join(failures))
        return 0

    print("=== GATE CHECK PASSED ===")
    print(f"  PR: #{number} {title!r} author={author}")

    _run_conversation(pr, event_context)

    print("=== RESULT ===")
    print("QA conversation completed; PR description updated via Auto-Test section")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:  # noqa: BLE001
        _fire_callback("FAILED", str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        code = 1
    sys.exit(code)
