# Canvas bounded dead-code

Created in OpenHands Cloud on October 13, 2026, after explicit approval.
**INACTIVE. Zero dispatches.** The January 1 UTC trigger is a staging placeholder,
not an agreed recurring schedule. The timeout is 1800 seconds. The saved
`deep-pro` model profile is selected explicitly; the account default is unchanged.

The method comes from [OpenHands/OpenHands PR #18417](https://github.com/OpenHands/OpenHands/pull/18417):
knip normal/production and TypeScript unused-local analysis, actual language-service
references, whole-repo search, a deletion experiment, baseline comparison, both
builds and byte-identical public declarations. The final tree must also pass full
suite comparison and applicable smoke/source/pre-commit checks. Historic baseline
failures do not excuse failures in a new run.

Each run may remove at most 5 symbols, change 10 production files and delete 200
production lines in one cohesive cleanup. Only verified work may produce one
**draft** PR. Existing overlapping work is skipped. Public API, telemetry/product
decisions, translations/assets, dependencies and broad refactors are excluded.
No merges, approvals, auto-merge, people mentions, reviews, issue writes or subagents.
If the checks cannot finish within the timeout, report BLOCKED without publishing.
No safe candidate is a valid NO_CHANGE result.

## Source and deployment

`tarball/` is the sole runtime copy, downloaded directly from the Cloud prompt
preset. Its `prompt.txt` is the maintained prompt. `automation.yaml` records the
observed settings; `deployment.json` records the source hashes and Cloud identity.
The request template in `definitions/canvas-dead-code/request.json` only supports
explicit creation. `scripts/deploy_dead_code.py create` refuses duplicates and
exports ONLY the newly created automation, leaving other bundles unchanged.

Git Sync is still disabled. Repository edits do not deploy. Review the current
Cloud bundle before any explicit update. The helper has no dispatch or activation
command. Choose a cadence and authorize a bounded first trial separately.

## Evidence and limitations

Deployment: authenticated account/integration checks, saved model-profile check,
atomic inactive creation, state/settings read-back, exact bundle download and
Python compilation. Offline tests cover inactive creation, duplicate rejection,
missing model, unexpected active read-back and prompt guardrails.

**No agent cleanup run has executed.** Language-service analysis, deletion safety,
full validation, draft publication and runtime cleanup remain untested end to end.
Prompt instructions bound the agent's authorized work; they are not a deterministic
code-deletion or GitHub permission gate. Concurrent manual dispatches could race
the open-PR check: keep first trials serial.

The standard Cloud prompt preset loads saved account credentials/MCP settings;
it does not enforce a per-automation secret allowlist. No secrets were copied,
changed or committed. Before a first run, review the integration identity,
fork write permissions, model budget and secret/MCP scope. The automation must
never expose secrets or change authentication. `keep_alive: false` requests
sandbox cleanup after completion; retain sanitized report evidence in the draft
PR/conversation before finishing, and verify lifecycle cleanup during the trial.
