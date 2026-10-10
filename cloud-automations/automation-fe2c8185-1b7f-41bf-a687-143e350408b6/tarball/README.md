# Attention router: daily verification reports

Proposed update to Cloud automation `fe2c8185-1b7f-41bf-a687-143e350408b6`.
The original weekly SDK selection/scoring is preserved on Mondays. The maintained
source is copied from the verified Cloud bundle; the dated export stays unchanged.
Git pushes do not deploy this source. Definition: `definitions/attention-router.json`.

Every weekday at 09:00 Europe/Amsterdam, the priority path reads OpenHands/OpenHands
PRs titled with `verify-openhands`, `map`, and `maintenance`, verifying a changed
path under `.agents/skills/verify-openhands/references/feature-map/`. It scans all
open PRs and seven days of recently updated closed PRs, with pagination, so a
report merged before the poll is still included. This deliberately recognizes
the established report series, not every docs PR or arbitrary QA report.

Reports go into the existing Review Notebook as `for_engel`, score 100, tagged
`daily-verification`, with current CI and bot-review status, the reported verdict,
total row and newly filed defects where those sections exist. It does not judge
whether the report's evidence is sound. No model completion is needed for this
path; the original full-context topic scorer still runs weekly. Notebook handled
state is preserved: the daily scanner does not erase a human's acknowledgement.
No WhatsApp delivery is wired by this change; that requires a separate decision.

For open non-draft reports only, request `all-hands-bot` using GitHub's reviewer
request API if current-head CI is green and no current-head substantive review or
pending request exists. Pending, failing, missing/unknown checks block the request.
Skipped/neutral check runs are allowed alongside an actual successful check/status.
Both legacy statuses and latest check runs are checked; pagination is mandatory.
Existing approval, changes requested or substantive comment on this head all count
as reviewed. A recognized CI-skip notice does not.

An atomic per-PR/head KV increment reserves at most one request attempt. No KV
means no mutation. An ambiguous/failed request is surfaced for manual follow-up,
never blindly retried. Head, draft and open state are rechecked before POST;
GitHub offers no atomic SHA-conditional review-request API, so a residual race
remains. This requests a review; it never approves, merges or posts a comment.

Uses the existing Cloud `REMOTE_GH` and `NOTEBOOK_INGEST_TOKEN` secrets. Before
deployment verify REMOTE_GH acts as the authorized maintainer and has review-request
permission. No additional credentials should be copied into Cloud. Public PR text
is data only; this code never checks out or executes PR code.

`DRY_RUN=1` prevents notebook, review and KV writes. `DAILY_ONLY=1` suppresses the
Monday broad scan. `VALIDATE_ONLY=1` also prevents writes. Tests use synthetic API
responses. A read-only live check can call `verification.run` with `dry_run=True`
without loading an SDK or LLM.

Deployment requires explicit upload/PATCH plus GET and source-bundle verification.
Keep the old definition/bundle for rollback. A schedule change alone is insufficient.
