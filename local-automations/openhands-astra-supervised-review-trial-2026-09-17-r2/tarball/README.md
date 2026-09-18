# OpenHands-Astra review auditor

**Status, 2026-09-16:** the ongoing Cloud automation is not enabled. Durable
Cloud KV and real event delivery remain deployment prerequisites. A separate,
operator-run manual pilot prepares selected PRs for explicit publication.
The planned OpenHands Automation uses native GitHub event triggers; there is no
cron schedule or polling service. Manual pilot results do not establish that the
ongoing Cloud automation is deployed.

## What starts a run

`registration.json` listens for `pull_request_review.dismissed` and
`issue_comment.created`, limited to `all-hands-bot` in:

- `OpenHands/OpenHands`
- `OpenHands/software-agent-sdk`
- `OpenHands/automation`
- `OpenHands/extensions`

The event wakes the controller. Canonical GitHub reads must establish an approval,
self-dismissal explaining that COMMENT was intended, and a strictly later,
unedited approval receipt, correlated by an explicit review ID/link or a unique
same-PR bot review episode within 60 seconds of dismissal. A replacement COMMENT
review is allowed; competing decisive reviews or dismissals make an ID-free
receipt ambiguous. Status metadata such as `state: APPROVED`, varied completion
wording and matching abbreviated commit SHAs are supported. Same-second timestamps
and ambiguous receipts do not qualify. A bounded three-second settling window
allows the event's REST timeline to catch up. There is no historical backfill.

The PR must remain open. Its author must have current write/maintain/admin access
or active membership in the owning OpenHands organization. Unknown eligibility
does not authorize a review. The reader credential must be able to establish this.

Intervene only where no one has already supplied a decisive review: any current,
undismissed `APPROVED` or `CHANGES_REQUESTED` review blocks the run, from any
account and at any commit. `COMMENTED` and `DISMISSED` reviews do not block it.
Check this before assessment and again immediately before publication. A previous
auditor intervention marker anywhere on the PR also excludes it permanently,
even after a new head or publisher change. Completed PRs are not reviewed again.

Operator configuration can also supply `retired_prs`, a list of
`{"repository": "OpenHands/automation", "number": 458}` entries, passed to
`Auditor(retired_prs=...)`. Repositories must be allowlisted and PR numbers must
be positive integers; malformed entries and case-insensitive duplicates fail
validation. These explicit exclusions stop event, manual and prepared-resume
processing before the controller reads state or GitHub, accesses the publisher,
or runs either model stage. Entrypoints must apply `validate_retired_prs` before
credential setup as well. Omitting the list preserves existing callers; marker
and durable-receipt retirement remain independent checks.

## What it does

1. Claim a durable repository/PR/head job using Automation KV compare-and-set.
2. Fetch full PR text, all closing-linked issues, exact Git source, the complete
   diff and every changed path. Preload changed-file contents when they fit;
   otherwise explicitly make their before/after text available through paged
   source reads. Read applicable `AGENTS.md` files as evidence.
3. Run the bundled `/codereview-roasted` skill with **`openai/gpt-6-astra`**,
   Responses API, high reasoning, through the explicitly selected route below. Save its independent
   assessment before revealing other reviews.
4. Start a fresh SDK conversation. Fetch all other reviews, inline comments,
   discussion and dismissal events. Check each at its own commit. Grade technical
   accuracy separately from policy compliance and truthful reporting. Preserve
   the original assessment; any revised conclusion requires explicit evidence.
5. Recheck PR head/base, description, linked issues, review evidence, author and
   eligibility. Save publication intent. Submit exactly one formal GitHub review
   with the selected APPROVE, REQUEST_CHANGES or COMMENT action.
6. Fetch the resulting review and verify actor, commit, marker and actual state.
   Record completion only from this receipt. A timeout never blindly retries POST.

The evidence fingerprint ignores nine cosmetic fields only on structurally
verified public repository objects: star/watch/fork counters, open-issue counters,
`updated_at`, and `pushed_at`. It also ignores `updated_at` on the exact
all-hands-bot CI-failing notice template for the current PR/head, after checking
the bot identity and comment URLs (including its timeline copy). The notice body,
creation date and identity remain hashed. Other comments and review receipts keep
their edit timestamps; substantive changes still stop publication. Saved hashes
from earlier versions are never migrated automatically.

The report leads with an emoji and the actual verdict, introduces
**OpenHands-Astra, helping Engel Nyst (@enyst)**, then gives its own assessment
and a compact bot score summary. The chosen voice is an
**exasperated robot babysitter**: friendly with the author, funny and incredulous
about the bot's contradictions. Use at most one good roast per review, backed
by evidence; credit the bot's real catches and keep humans out of the roast.
Scores are 0 material miss, 1 minor miss, 2 defensible
50-50 judgment, 3 supported judgment, or unscored with a stated evidence gap.
The bot cannot merge PRs or dismiss another review.

Public reports aim for **60–100 words before expansion**. Use a clear verdict
line, the shortest complete reason, actionable findings and essential caveats,
then one robot joke. Put per-review scores, reasons and receipts inside a
GitHub `<details>` block so the evidence is available without dominating the PR.
Every material finding stays visible; several findings can exceed the soft word
target. Never shorten a sentence by cutting away a condition or uncertainty.
Full analytical paragraphs and all evidence remain in the saved structured
result. Emoji and layout provide the default visual cue; no external GIF is
required, and humor must never carry information missing from the text.

## Where runs execute and which model they use

The SDK adapter permits exactly two explicit Astra routes:

| Selection | Transport | Requested model |
| --- | --- | --- |
| `native` (default) | Native OpenAI Responses API | `gpt-6-astra` |
| `eval_openrouter` | Eval proxy through OpenRouter | `openrouter/openai/gpt-6-astra` |

Both select the actual Astra model. The proxy route is an operator choice,
**not an automatic fallback**. The current Cloud entrypoint selects the native
route; the manual pilot can explicitly select `eval_openrouter`. Receipts retain
the canonical model, configured SDK model, selected route and reported usage.
They do not independently prove a provider's internal routing.

### Manual preparation and publication

`Auditor.handle_manual(repository, number)` is preparation-only and requires
`publish=False`. It checks the same correlated review pattern, author eligibility
and open PR state as event processing, without fabricating a webhook. A manual
batch selects at most ten PRs, saves each original input and durable checkpoint,
and exports an immutable result before any publication.

The pilot only admits public evidence. It rejects private repository references
and pending draft reviews, and checks linked-issue text against unauthenticated
GitHub responses. Credentials stay outside model inputs.

Publication is a separate explicit step:
`Auditor.resume_prepared(repository, number)` requires `publish=True` and exactly
one already audited matching job. It preserves the original run identity and
rechecks source, PR text, linked issues, reviews and eligibility before submitting
the selected formal review. It cannot silently start a new assessment or reset an
uncertain publication. The local pilot uses atomic filesystem checkpoints;
these do not satisfy the future Cloud automation's KV prerequisite.

## Supervised local Automation trial

The `manual-trial` entrypoint is dispatched by the local OpenHands Automation
service itself. It requires a native macOS host, persistent host storage,
`eval_openrouter`, the matching explicit model profile and publisher `enyst`.
The deployment bundle fixes one to four repository/PR/head/base targets. An
injected event payload or any event filter other than literal `false` is rejected.
Previously completed PRs are excluded before credentials or GitHub reads.

Each PR uses the same two-stage controller and fresh publication checks as event
processing. The run saves structured stage results and atomic, locked per-PR
checkpoints before posting. It renders the concise report automatically; there
is no separate editor or publisher step. A saved trial index prevents automatic
reruns, including after failure or an uncertain POST. Recovery requires separate
operator investigation. This host storage is never used as a Cloud fallback.

For service versions that require enabled definitions for manual dispatch, the
always-false event filter remains in place during the trial; the definition is
disabled after completion. The service timeout is 30 minutes, model-stage starts
and publication have a 25-minute deadline, and each provider request has a
15-minute timeout. An in-flight request can outlast the stage-start deadline;
the persistent publication intent prevents blind retries after interruption.
Callbacks permit HTTP only to literal loopback hosts in this explicit local mode.
The active user profile is unchanged and credentials never enter model input.

## Execution boundaries and limits

- The model can only read and search admitted Git commits. It gets no terminal,
  file editor, GitHub tool, secrets, plugins, hooks or shared conversation history.
  Source fetches use a fresh bare Git store without inherited credentials.
- The controller owns credentials and GitHub publication. Future reviews use
  **enyst**. The publisher's `/user` identity must match the configured login
  before model work starts. For enyst-authored PRs, the controller stops before
  assessment or publication; it does not switch to another account.
- Source assessment does not execute PR code or tests. Reports must say so.
  Text may quote prior reviews in a PR description; instructions exclude those
  claims from Stage A, but this cannot guarantee perfect blinding.
- Complete diffs and returned source contexts have a 1 MiB budget. If full
  before/after file contents exceed the preload budget, both stages receive the
  complete diff and manifest plus an explicit instruction to read files in pages.
  Internal text blobs are bounded at 8 MiB; model tools return at most 400 lines
  and 128 KiB per page. Full PR descriptions and linked-issue text are preserved.
  Binary or other source errors are not caught by the preload-budget fallback;
  unavailable evidence must be disclosed. Excessive prompts and oversized
  reports still stop the run without silent cuts.
- The current-head diff is always complete. Historical patches are preloaded
  together only while their combined serialized size fits 256 KiB. If that
  budget, or one historical patch's individual limit, is exceeded, **all**
  historical entries instead carry complete changed-path manifests, exact
  base/head commits and an explicit on-demand source note. No patch is sliced.
  Every historical commit remains available through read/search tools; reviewers
  must inspect evidence at its own commit or leave the technical score unscored.
- Large Stage B inputs share repeated full text, actor metadata and evidence
  objects through explicit reference tables. This is a lossless encoding: every
  field, review, array position and commit attribution remains available. Tests
  verify exact decoding; the locked independent judgment and operator
  instructions remain outside the encoding. The complete prompt still has a
  2,000,000-character limit and stops before a model call if it exceeds that.
- Both stages are pinned to SDK 1.46.0 and one of the explicit Astra routes above. The SDK
  adapter restricts ambient plugin/profile loading with version-specific hooks.
  Upgrade only after its real-initialization isolation tests pass.
- KV has a 64 KiB total-document limit. This bundle reserves 4 KiB, retains compact
  publication receipts indefinitely, and stops at capacity. Failed jobs require
  operator recovery. Pre-publication leases last 45 minutes, longer than the
  30-minute run timeout. Publication-intent/unknown jobs never auto-transfer.
- GitHub and KV do not share a transaction. A final GitHub read cannot prevent a
  subsequent upstream change. An uncertain publication remains blocked for
  manual reconciliation; exactly-once delivery is not promised.

## Deployment prerequisites

The Cloud API validated the event types and four-repository filter. That proves
matching, not delivery into the intended Cloud organization. Resolve and observe
actual delivery before enabling this automation. A repository filter does not
change ingress routing. Do not claim the whole OpenHands Git organization into
a personal Cloud organization as an incidental setup step.

The live KV probe returned HTTP 503, and a temporary Cloud run received no KV
authorization token. Durable state is unavailable in that runtime; there is no
stateless publishing fallback. Keep the ongoing Cloud automation disabled until
its own durable-state and event-delivery checks succeed. A successful manual
assessment or publication would not resolve either prerequisite.

`config.json` refers to Cloud credentials by name only. Both reader and publisher
use `REMOTE_GH`, previously verified as **enyst**; `publisher_login: enyst`
requires a fresh identity check on every run. For manual publication, use enyst's
GitHub integration or enyst's Keychain token and verify the authenticated login.
Do not use smolpaws as the publisher. Confirm current permissions before any
future deployment. The configuration remains `mode: dry-run`; changing the
credential selection does not enable or deploy the automation. Historical pilot
artifacts keep the identities and receipts they actually used.

The custom Cloud bundle resolves its configured credentials inside its authorized
sandbox and pins Astra itself;
it does not use a default profile as fallback. The current Cloud entrypoint
requires a profile named `astra-review-auditor` configured for the same native
model, so the visible Automation model selection agrees. Enabling the proxy
route in that entrypoint requires a separate explicit configuration change.

Once prerequisites are verified:

1. Review the concrete registration and bundle. Package with `python package.py
   review-auditor.tar.gz`; upload raw gzip bytes to the Automations uploads
   endpoint, and replace the registration's placeholder URI with its result.
2. Stage with a literal-false event filter. Raw creation defaults enabled;
   `enabled` is not a create field. Manual dispatch requires an enabled definition,
   so keep that false filter in place during bootstrap. Keep `keep_alive: false`.
3. With `mode: bootstrap`, dispatch once serially to initialize KV, then disable
   the definition while updating its bundle and trigger. Then package
   again with `mode: dry-run`. Dry runs retain the assessment in KV and never post.
   Local historical fixtures can test detection, but closed PRs are always skipped
   by the production controller. Do not forge a live webhook for old PRs.
4. Validate the dry result and real event delivery. Configure `mode: live` only
   for the final enabled registration, with its supported native event filter.
5. Reconcile any prepared/failed/uncertain job explicitly before resuming it. Read
   GitHub by the exact marker and actor; do not delete state to force a repost.

Runtime sends one completion callback after all stages and publication finish.
It records sanitized status/error codes; credentials and raw provider errors are
never printed. Failed callbacks exit nonzero. Model conversations are local SDK
conversations without persisted transcripts; durable stage results live in KV.

## Development

From the repository root, with SDK 1.46.0 and pytest installed:

```sh
python -m pytest -q tests/test_review_auditor_*.py
```

Tests use fake HTTP/model transports and local Git fixtures. They do not spend
model tokens or write GitHub/Cloud resources. The bundled review skill and risk
reference are unchanged copies from Extensions commit
`6242eacba016bf85009d8e3f5b8aa131295e278c`, under the repository's MIT license.

Investigation and decisions:
https://enyst.github.io/arch/openhands-review-auditor.html
