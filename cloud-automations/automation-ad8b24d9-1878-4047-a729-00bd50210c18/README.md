# Canvas dead-code historical canary

One explicitly authorized manual discovery trial, October 13, 2026.
The automation remains INACTIVE; its January staging trigger is not a schedule.
Target: `5a4071c764a9323c599e70258211b3f67c2aefad`.
The target was verified as the first parent of the relevant cleanup merge,
not the PR's moving base SHA. The prompt and source do not name or link any
reference PR, author, findings or diff. The agent must discover independently.

The same verification requirements apply as in the regular Canvas dead-code job.
No cleanup size limits. The historical trial does not consult overlapping cleanup
PRs or newer source, and may open one draft PR with proven removals. It must
prominently disclose its historical target and must not rebase onto new main.
No merge, approval, auto-merge or destructive Git operations are authorized.

`tarball/` is the sole runtime source downloaded from Cloud. The model is saved
`deep-pro`; timeout 1800 seconds. `keep_alive: true` temporarily retains evidence:
retrieve the report/patch and stop or pause the owned sandbox after the trial.
The standard preset uses account secrets/MCP settings, not a secret allowlist.
No credentials were copied or changed. Runtime model/analysis/publishing are not
proven just by creation. Evidence and outcomes go in the dated evaluation receipt.

`scripts/run_dead_code_canary.py create` refuses duplicate definitions;
`dispatch` verifies exact identity and inactive state, and refuses a second run.
Repository changes do not deploy through Git Sync.
