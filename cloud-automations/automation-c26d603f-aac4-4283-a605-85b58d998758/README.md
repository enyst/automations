# Codex ACP Canvas verification canary

A manually dispatched, inactive experiment. OpenHands Cloud supplies the
sandbox, GitHub integration and saved account secrets. Codex runs through ACP,
using ChatGPT authentication rather than Codex Cloud or an OpenAI API key.

## Scope

One desktop `F02.sidebar-collapse` recipe from `OpenHands/OpenHands` at
`d7e14e25bcf14238e566139ec9490e1f05efcd69`. No product or harness edits,
public writes, subagents, recurring schedule or model-backed Canvas conversations.
The coordinator sends **no `secrets` payload**. The app server resolves the
selected profile and saved account credentials.

`agent-profile.json` records the dedicated profile specification. It is not
activated as the account default. `automation.yaml` remains INACTIVE with a
January staging schedule. `tarball/` is the sole runtime source. Pushing this
repository does not deploy it; use `scripts/deploy_codex_canary.py` explicitly.
The sandbox is temporarily retained for evidence inspection; pause it after
retrieving evidence, or cancel the owned trial when blocked. Do not leave it
running unattended.

## Credential compatibility

The saved profile restricts secrets to `CODEX_AUTH_JSON`, `DEEPSEEK_API_KEY`
and `GITHUB_TOKEN`. Cloud's Agent Server 1.50.1 did not carry that restriction
into the conversation on the first trial. Engel explicitly accepted the legacy
all-account-secrets behavior for this experiment on October 12, 2026.
`allow_legacy_account_secrets` records that approval; set it to false once the
Cloud runtime supports the restriction. Do not infer successful restriction
from profile save/read-back alone.

No credential was copied from the Mac or included in repository files, uploaded
bundles, prompts or conversation-create payloads. No API-key fallback is selected.

## October 12 trial

- Automation: `c26d603f-aac4-4283-a605-85b58d998758`.
- Dedicated profile: `55383c97-f65f-44c8-a81a-3fa65a41b39b`.
- First run was cancelled before a Codex turn while checking secret scope.
- Second run created an ACP/Codex conversation on the selected repository with
  `gpt-6.1-sol`. Codex startup then returned `ACPAuthRequired` and
  `CredentialNeedsReauthentication`: “ChatGPT authentication did not complete
  in time. Please sign in again.”
- No Codex tool action or UI recipe ran. Authentication, model availability,
  browser setup, callback completion and live verification remain **unproven**.
- Both owned runs were cancelled. Read-back found neither sandbox remaining.
  The automation remains inactive and the account's active profile is unchanged.

The error does not establish whether the saved credential expired, refreshed
elsewhere, or encountered another authentication problem. Do not overwrite it
or select a paid API-key fallback without explicit approval.

See [trial.json](trial.json) for the non-secret receipt. A next trial needs working
Cloud Codex authentication. The coordinator starts the conversation idle, checks
its declared agent, then sends the run message; its completion checks require the
scoped JSON result and verified cleanup, not merely a finished conversation.

## October 13 fresh-login trial

A separate device login was stored in Cloud `CODEX_AUTH_JSON`, with explicit
approval to replace the older Cloud credential. The existing Mac `auth.json`
was checked before and after and was unchanged. The new credential was never
committed or included in the automation bundle or conversation request.

The fresh login authenticated successfully. The ACP adapter reported actual
model `gpt-6-astra`, although the profile and app metadata named `gpt-6.1-sol`.
Treat the adapter's current-model observation as authoritative for this trial.

The idle-start follow-up initially lacked the complete task; Codex asked for
it again. A manual resend of the entire bounded task allowed the real recipe
run. All **16 desktop sidebar assertions passed**, including reload persistence,
60-pixel width, hidden/restored checklist, tooltip, backend popover, both expand
paths and zero application browser errors. Five screenshots and the scoped
transcript were retained privately before pausing the sandbox.

The final verdict remains **blocked**. All five verification ports were closed,
but the launcher and browser were PID-1-owned zombies. The harness refused its
private-state purge. No broad kill or PID-1 manipulation was attempted.

The coordinator also found that this Cloud deployment omitted
`AUTOMATION_CALLBACK_API_KEY`. The repaired source now uses the existing
`OPENHANDS_API_KEY` fallback, as other Cloud automations do, and resends the full
task in the run message. Both repairs were deployed while the automation
remained INACTIVE. They have offline regression coverage but have not yet had a
fresh automatic end-to-end replay. The observed trial's FAILED callback was
sent manually, and its containing sandbox was verified PAUSED.

Authentication now works at least for this one fresh run. Long-lived refresh,
parallel credential use, the requested Sol model selection and fully unattended
completion remain unproven. This is sufficient evidence to continue developing
the route, not to enable a daily maintenance fleet yet.
