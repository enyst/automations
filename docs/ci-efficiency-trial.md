# CI-efficiency audit: first Cloud trial

Inspired by the CI-history ranking idea in [Maintaining software with the GPT-6 family](https://developers.openai.com/blog/maintaining-software-with-the-gpt-6-family).

## Deployed October 17, 2026

- Automation: `09ec7d31-cd3a-42b4-8133-d1a7c41f946e`.
- First manual run: `e1e7b95f-9b36-4ee7-bead-f56b02bf88ab`, dispatched 14:11:31 UTC.
- Recurrence: **disabled**. January 1 is a staging placeholder, not an active schedule.
- Repository: `OpenHands/OpenHands`, `main` recorded at checkout.
- Model: existing `transpile-deepseek-pro-native` profile. Cloud did not have a Sol profile when this trial was created. This tests the workflow idea, not the article's model comparison.
- Deadline: 25 minutes including setup; prompt requests completion by 20 minutes.
- Keep-alive: false. No existing automation was updated.

The six deployed files were downloaded and compared byte-for-byte with the
uploaded archive before dispatch. Archive SHA-256:
`ffbeece9c8479c58d6dd739ed3fd870c273d28fb1bec5756f74f062a663f47c0`.

## Question and evidence

Rank up to five CI efficiency improvements by estimated runner-minutes saved per
day. Use seven complete UTC days of Actions history, capped at 500 workflow runs
and 20 detail/log requests. Report sampling, caps, missing access, exact arithmetic
and run links. Separate runner time from workflow wall time, queue time and billing.
Do not add overlapping estimates together. Require a verification experiment for
each suggestion and check open PRs for overlap.

This run produces proposals, not fixes or measured savings. It must preserve
security checks, test coverage, platform validation and merge/release semantics.
Insufficient evidence is an acceptable `INCONCLUSIVE` or `BLOCKED` result.

Expected private sandbox artifacts:

- `/workspace/ci-efficiency-report.md`
- `/workspace/ci-efficiency-evidence.json`

The final finish-tool answer must summarize findings and caveats. Retrieve the
artifacts before treating this trial as successful; dispatcher acceptance alone
is not an audit result. Do not publish raw run logs or unrelated private context.

## Boundaries

The prompt authorizes read-only GitHub investigation and local report artifacts.
It forbids repository edits, branches, PRs/comments, workflow reruns/cancellation,
settings changes, Slack access and pings. The runtime verifies the `smolpaws`
GitHub identity and injects only the existing `SMOLPAWS_TOKEN` lookup (also bound
to GitHub CLI aliases). It disables MCP and fails if its model profile is missing.
The bot token is not inherently read-only; these task limits are prompt constraints,
not a fine-grained GitHub permission guarantee.

Daily `verify-openhands` logs are **disabled** for this first trial. Verification
coverage is not production telemetry. Absence from verification logs must never
be treated as proof that a production path is unused.

## Explicit operations

From this repository:

```sh
python3 -B scripts/deploy_ci_efficiency.py dry-run
python3 -B -m unittest discover -s tests -p test_ci_efficiency.py -v
python3 -B scripts/deploy_ci_efficiency.py status --automation-id 09ec7d31-cd3a-42b4-8133-d1a7c41f946e
```

`create` and `dispatch` are separate, explicit write commands. Create refuses a
same-name duplicate. Dispatch verifies account, inactive definition, exact bundle,
and absence of an active run. Do not blindly retry an ambiguous dispatch. Nothing
runs on import, and neither command enables a schedule. Git Sync remains disabled;
committing this backup does not deploy it.

Review the first evidence before enabling a recurring schedule or allowing a small
follow-up implementation. No auto-merge or autonomous follow-up is configured.
