# Local launcher, Cloud verification worker

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

The worker uses `/verify-openhands` for one behavior: **F02.sidebar-collapse**,
desktop. It must use actual browser actions, retain screenshots and per-assertion
evidence, stop its own stack, verify cleanup and write the result/report. No
product/harness edits or public writes are authorized. This is a single behavior
trial, not a daily full-map job.

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

Private state under `~/.smolpaws/automation-state/cloud-sidebar-verification/`
is stable across local automation runs. A file lock serializes submissions;
an atomic state write records intent before each create/send. Repeated runs
monitor the same worker instead of launching another. Ambiguous POST responses
stop with the saved ID for inspection instead of automatically resending.
`python3 main.py --status` reads state/status without starting new work.

If startup is still pending when this short launcher exits, dispatch the same
local definition again to finish startup/send, using its retained ID. This
initial version does not automatically schedule monitoring or another trial.
On terminal completion, collect `VERIFY_RESULT.json`, `VERIFY_REPORT.md` and
sanitized evidence from the Cloud worker, then pause its owned sandbox. Never
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
