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
