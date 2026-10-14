# Local launcher, Cloud daily verification worker

A local OpenHands **custom script automation**, built from the extensions
`openhands-automation` skill. The script uses only Python's standard library;
no local LLM or verification stack is involved. It submits one normal OpenHands
worker in a fresh OpenHands Cloud sandbox, then exits. Its completion callback
means **submission/control-plane completion**, not a verified UI pass.

## Repository and task

The sole runtime source is `tarball/`. The repository is declared in
`tarball/repos_config.json`: `OpenHands/OpenHands`, `main`. The script maps that
configuration to Cloud's `selected_repository` / `selected_branch` launch fields,
so **Cloud clones the repository**. Custom script automations do not automatically
consume `repos_config.json`; the script must forward it. There is no local clone,
installation, server launch or browser command, nor a clone instruction in the
worker prompt.

The worker follows `.agents/skills/verify-openhands/references/daily.md` and its
linked procedures. Subagents are allowed, each with its own verification run.
One maintenance PR may contain proven map/harness corrections; product defects
are reported, not fixed. Existing issues receive new reproduction evidence
instead of duplicates. No merges. Model-backed verification requires an explicit
budget; otherwise it is marked blocked. The prompt does not duplicate the skill.

The directory keeps its original name to preserve the deployed automation's
history. The October 14 sidebar trial remains a separate historical result;
it is not evidence of a completed daily pass.

## Authentication and state

The launcher reads only the existing local `OPENHANDS_API_KEY` secret via the
Agent Server settings API. It validates the Cloud account. No keys are copied
into code, prompts, subprocess environments or conversation-create payloads.
The Cloud worker uses API-authenticated `openhands/deepseek-v4-pro`, not ACP or
ChatGPT login. Request idle creation, validate the actual agent/model, then send the task only
if still idle. Some Cloud versions already run the initial message; in that
case, record the submitted worker without sending the task twice.
Cloud uses its saved account settings/integration; the current runtime can expose
all saved account secrets/MCP settings. This is not a per-worker allowlist.

Private state under `~/.smolpaws/automation-state/cloud-daily-verification/`
is stable across local automation runs. A file lock serializes submissions;
an atomic state write records intent before each create/send. Repeated runs
monitor the same worker instead of launching another. Ambiguous POST responses
stop with the saved ID for inspection instead of automatically resending.
`python3 main.py --status` reads state/status without starting new work.

If startup is still pending when this short launcher exits, dispatch the same
local definition again to finish startup/send, using its retained ID. This
initial version does not automatically schedule monitoring or another trial.
On terminal completion, collect the daily report and sanitized evidence from
the Cloud worker, then pause its owned sandbox. Never
apply the local launcher's timeout to the remote verification conversation.
This evidence retrieval/cleanup is an operator step for this first trial.

## Installation and scope

The definition is **INACTIVE**, with a January staging placeholder and no agreed
recurring schedule. `scripts/deploy_local_cloud_verification.py dry-run` packages
and syntax-checks without network calls. Its `install` command targets only the
identified local backend, validates/uploads the bundle and creates it paused.
It does not dispatch. Git push alone does not install; do not invoke global
Git Sync and import unrelated definitions.

Offline tests exercise the packaged script with synthetic Cloud replies,
including successful submission, repeated-run idempotence, uncertain-send
handling, ACP rejection and local-only execution. A live verification result
must not be inferred from these tests. The local secret was checked against the
expected Cloud identity without copying or logging it; the Cloud account's
active agent profile was unset at preflight.

## First trial · October 14

Installed paused and manually dispatched after approval. Local submission completed;
one normal OpenHands/DeepSeek-Pro Cloud worker is running. The existing local
stack was not restarted. Cloud unexpectedly ran the initial message immediately;
the launcher now handles that without sending the task twice. Result and remote
cleanup are still pending. See `deployment.json` for identities.

## Daily prompt replacement · October 14

After the launcher PR merged, the same inactive local definition was renamed
and its bundle replaced with the minimal daily prompt. No daily run or schedule
was enabled. The new task uses a separate state directory so it never sends a
daily task into the finished sidebar conversation. This launcher still submits
one worker and then monitors that saved worker on subsequent dispatches; it is
not yet a recurring daily-run lifecycle manager. Choose model-backed test budgets
and a recurrence policy before enabling a daily schedule.
