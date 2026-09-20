# Notebook Field Notes

Cloud ID: 623cc664-07c2-425e-bc99-8da3d43c4206.

Approved September 19, 2026: autonomous investigations are public by default.
This is a custom OpenHands Cloud automation. Its publication permission applies
only to newly generated Field notes; it never publishes Engel's drafts or working
references.

## Flow

1. Poll public issues and PRs in OpenHands/OpenHands, software-agent-sdk and
   automation. Normalize the agent-sdk alias. Consider open, merged and closed
   subjects, excluding draft PRs and recent activity still settling.
2. Apply deterministic scope, quiet-period, duplicate and budget checks.
3. Ask pinned Jev `jev-1.13.0` five independent questions using only the PR/issue
   title and description plus up to four linked issue titles and descriptions.
   No diffs, fetched code or discussion comments enter this classifier. Its
   state is capped at 24,000 UTF-8 bytes; this is a transport bound, not a model
   token limit. Coverage records failed retrieval and any truncation.
4. A bounded OpenHands SDK conversation investigates pinned public code through
   its sole read-only evidence tool. It has no terminal, filesystem, MCP or
   publication tool. Dates, destinations and provenance are added by code.
5. Validate the Markdown artifact, check the source is still current and the
   run still owns its lease, then create JSON and standalone HTML in
   enyst/enyst.github.io/field-notes. One nonforcing Git commit adds both files,
   the hashed manifest and the generated index.
6. Liberty Labs' separate insert-only importer adds new public Markdown records
   to its D1. Existing edits, visibility decisions and reserved IDs remain intact.

One original note is published per PR/issue. Later source activity does not
rewrite it. Notes describe the examined revision; they are autonomous
interpretations, not endorsements or claims of executed tests. Evidence links
are checked against examined repository/commit pins; this is not a semantic
proof of every cited statement.

## Description triage · policy 3

Policy 3 is deployed; the dated validation section below records its Cloud trial
and read-back. Earlier validation sections retain the observed history.

The five exact keys are `design`, `agent_behavior`, `memory`, `substance` and
`design_context`. With complete, untruncated input, writing requires any of the
first three topic probabilities at least 0.65 and substance at least 0.70.
Otherwise the topic is skipped. Context does not gate note selection.
Cross-repository interaction is no longer a separate score, but `cross-repo`
remains a valid descriptive note tag.

Separately, `design_context` at or below 0.30 sets a `needs_info` Boolean. It asks
whether the description is ready enough to begin implementation, not whether the
topic is interesting. A clear routine fix can be ready; the author need not solve
an open design question or identify a bug's cause first. Higher context values
do not cause a clarification request or defer an otherwise selected note.
These are independent estimated probabilities, not an overall score or a
ranking. Exact questions and examples live in [RUBRIC.md](RUBRIC.md).

The independent needs-information flag may produce one fixed, transparently
attributed comment asking the author to update the PR description or linked
issue with the problem, affected components and intended behavior. Comments
are limited to **one automatic attempt per open,
nondraft PR**, and **two comment attempts per UTC day**, separately from the
writing budget. Description triage and comment checks continue when the writing
allowance is exhausted and after the invocation's one writing investigation.
Issues and closed or merged PRs do not receive these comments. A selected PR
may receive a note and a clarification request independently.
The script checks current descriptions and the source revision again before
posting. It persists a reservation first, and never repeats an uncertain POST.

Failed or incomplete retrieval and truncated input always **defer**, even when
Jev's probabilities are high. They cannot trigger a request blaming an author
for missing context. Descriptions can be evaluated again after the 24-hour
cooldown, including improvements in linked issue descriptions; this does not
authorize another comment on a PR already asked. Classifier policy version 3
invalidates old decision-cache entries. Fully delimited Jev Fast Audit
scorecards are excluded from classifier input to avoid feeding prior scores
back into the model, while original descriptions remain intact.

Live classification accepts exactly the five policy 3 keys. Artifact validation
also accepts the exact policy 1 set (`design`, `agent_behavior`, `memory`,
`cross_repo`, `substance`) and the policy 2 set (those five plus `design_context`).
No other missing or extra score keys are accepted. Existing artifacts and
`cross-repo` tags are preserved.

## Bounds and state

The desired schedule is hourly at minute 15 UTC. At most 12 candidates are
considered in a run, one writing investigation runs per invocation, and two
writing attempts are allowed per UTC day. These writing caps do not consume or
stop the separate comment allowance. A failed investigation after budget
reservation consumes that day's allowance; technical writer initialization
checks run before reservation. Failed or deferred investigations retry after
24 hours. A selected topic waiting only for a writing slot retains its validated
Jev scores in the bounded state cache. When a slot opens, fresh unchanged source
can use that judgment without another Jev call or an extra day's delay.
The SDK writer is limited to 16 turns, 24 public source requests and a $2 budget.
It still investigates pinned public source after selection; the description-only
restriction applies to Jev's initial triage.

There is no persistent queue of interesting items awaiting tomorrow. Each run
rescans the rolling seven-day updated window, subject to the configured start
date, and considers the oldest eligible update first. Unprocessed items can be
rediscovered on later runs while they remain in that window; they can age out.
An edit only to a linked issue does not bring an old PR back into that window.
The daily writing cap does not promise eventual publication of every candidate.

Cloud capabilities did not advertise kvStore on September 19. Durable run state
therefore lives in state.json on the dedicated notebook-field-notes-state
branch of enyst/automations. The repository may be public or private; verification
requires that exact repository and an explicit visibility value from GitHub.
GitHub's content SHA is the compare-and-swap guard for lease and state updates.
It does not modify main. State contains public subject identifiers, fingerprints,
status and timestamps, bounded cached selection scores, daily counters, comment
receipts and an expiring lease;
it contains no credentials, discussion text or generated drafts.
Leases expire after 35 minutes, longer than the 30-minute Cloud run timeout.
Daily reservations precede writing, and publication verifies the current lease.
Completed public manifest entries remain duplicate guards after state pruning.

The launcher fetches only TYPESAFE_API_KEY and the integration's github_token.
The OpenHands writer loads the account's default model configuration with
get_llm(profile_name=None). It deliberately does not inherit AUTOMATION_MODEL:
Cloud initially filled that field with an unrelated evaluation profile whose
provider credentials failed. No credentials or model keys are copied into Git.
Credentials are never embedded in the bundle, note, URL, state or logs. PR text
cannot choose commands, credential names, repositories or publication paths.

## Deploy deliberately

Pushing Git does not activate the automation. The deployment helper preserves
the Cloud-to-Git export convention:

```sh
python3 -B scripts/deploy_field_notes.py preflight
python3 -B scripts/deploy_field_notes.py stage --classify-only
python3 -B scripts/deploy_field_notes.py dispatch --automation-id ID
python3 -B scripts/deploy_field_notes.py stage --automation-id ID
python3 -B scripts/deploy_field_notes.py dispatch --automation-id ID
python3 -B scripts/deploy_field_notes.py enable --automation-id ID
python3 -B scripts/deploy_field_notes.py status --automation-id ID
```

Stage creates or updates a paused definition with a harmless annual placeholder
schedule. Cloud only permits manual dispatch of enabled automations, so dispatch
temporarily enables this parked schedule; enable later installs the hourly one.
Its runtime bundle is a nine-file allowlist, including the description collector
and bounded comment publisher in `descriptions.py` and `comments.py`; tests, environments and
unrelated files cannot enter it. Manual trials precede enabling the desired
schedule. Use --keep-trial-sandbox only when inspecting a trial, and remove it in the final
staging upload. A successful upload is not evidence of successful execution: inspect
the run and resulting public artifact, then export Cloud definitions using
scripts/export_cloud.py. To stop future runs, use the pause command.

## Tests

```sh
python3 -B -m unittest discover -s tests
python3 -B -m unittest discover -s sources/notebook-field-notes -p test_core.py
```

The writer integration tests additionally require SDK/workspace 1.49.2 and use
mocked model/network responses. They do not call a paid model or publish.


## Initial policy 1 live validation · September 19, 2026

This records the initial deployment before the description-only policy 2 update.
It is historical execution evidence, not a claim that a later source edit is live.

The hourly definition was enabled and read back, with keep-alive disabled and
the normal two-attempt budget restored. The observed Cloud model field retains
its account-assigned evaluation profile; this custom runner intentionally uses
the account defaults instead, as documented above.

Classifier-only run ca196acd-d47c-4bd1-a0bc-fe34c47c88c5 completed without
publication. Two subsequent trials failed model authentication before generating
a note: Cloud had inherited a named evaluation profile. A minimal default-model
completion passed; the runner now explicitly calls get_llm(profile_name=None).
The failed attempts remain in the run state. One additional setup
attempt was explicitly staged with a limit of three, then the limit was restored.

Run 73517770-eb45-42a8-bdd1-ce9ec0e0aae8 completed the real chain: eight subjects
considered, two classified, one investigation written and published. The note
about SDK PR #5164 was committed to enyst/enyst.github.io at
30205f565f0a6171341465234cb6c30190d4bb79. Its artifact hash and four key pinned
citations were independently checked. No runtime or test execution is claimed
by the generated note.

At that validation milestone, the suite passed 138 tests plus 18 pure classifier/artifact tests, including
the real pinned SDK with mocked model/HTTP transport. The Cloud export's seven
runtime files match maintained source byte-for-byte. Liberty Labs imported one
new record, preserved all 95 existing records and 13 private drafts, and a
scheduled repeat import inserted zero. The local reader was visually checked
at desktop and phone widths. No Cloudflare website deployment occurred.

## Policy 2 validation · September 19, 2026

The final classification-only Cloud run
d63e7c2b-92a6-4b10-b1eb-953d8ae28bda completed with exit code zero: 12 subjects
considered, six classified, four selected, one deferred and one skipped. It
wrote no notes, posted no comments and did not modify durable run state. The
deferred PR had strong topic scores but incomplete linked context, demonstrating
that the coverage guard overrides a positive model result.

All 176 automation tests and 26 pure policy/artifact tests passed with the
pinned SDK available. The comment boundary was tested with synthetic GitHub
transport, including incomplete input, fixed text, exact-source freshness,
at-most-once reservations, pagination, uncertain POSTs and independent counters.
No live comment was posted as a test.

Liberty Labs passed 241 TypeScript and 26 WebMCP tests, build, type check and a
Wrangler dry run. Its importer accepts both old five-score and new six-score
artifacts. The existing note was visually checked through the local Tailscale
preview at desktop and phone widths. No site deployment occurred.

The hourly definition was re-enabled and read back with publication mode,
keep-alive disabled and the normal two-attempt writing limit. The final runtime
bundle SHA-256 was
612a59ba0e298e9304126f70f3ea72726e3de14ff1d74f0ab04ea56dac282d41,
identical to the final classification-only trial's runtime bundle.


## State visibility support · September 20, 2026

The state repository may now be public or private. Verification still requires
exactly enyst/automations and a Boolean visibility field; malformed metadata is
rejected. The lease, content-SHA concurrency checks and public-only source and
publication boundaries are unchanged.

Validation passed 175 automation tests and 26 pure policy/artifact tests; four
SDK integration tests were skipped because the SDK was unavailable in this test
environment. The packaged guard passed synthetic public/private cases and a
read-only GitHub metadata check. Cloud read-back confirmed that only transport.py
changed, with the existing enabled hourly schedule and all other settings
preserved. No publication run was dispatched for this change. The repository
remained private. The deployed bundle SHA-256 was
24cf3d28ba8eb9d89698be133568fd5473c90a35d0711909ac675a8cede6114b.


## Policy 3 validation · September 20, 2026

Removed cross-repository interaction from Jev's selection questions. Note
selection now depends only on design, agent behavior or memory, plus explanatory
substance. Implementation readiness produces a separate clarification flag;
a selected topic can receive a note and an explanatory comment independently.

All 204 automation tests and 26 pure policy/artifact tests passed with the pinned
SDK available. Red/green coverage includes low-context research and comments,
independent budgets, next-hour reuse of waiting selections, immutable publication
receipts after collection failures, material freshness after our own comments,
and bounded cache size without evicting comment receipts. An independent review
found and verified the fixes for these retry and state interactions.

Classification-only Cloud run 278f2c30-46bc-4787-bd53-2d7c84044333 completed with
exit code zero: 12 subjects considered, six classified, four selected, one
deferred and one skipped. Output contained exactly the five policy 3 scores.
No item triggered the clarification threshold in this sample; low-context
behavior is covered by the synthetic tests, not a live author comment.
The trial wrote no notes, posted no comments and changed no durable run state.

The same runtime bundle was staged in publication mode and the hourly schedule
read back enabled, with keep-alive disabled and the existing limits preserved.
Its SHA-256 is
06c3bb3e75ecee1ed68850554e36afd07c53901f758b989471b72e77f1a9c1f9.

Liberty Labs' importer passed all 268 tests, build, type check and Wrangler dry
run. It accepts the exact policy 1, 2 and 3 provenance sets, preserving existing
tags and artifacts. The frozen 270-issue evaluation still validates unchanged;
this policy update does not revise its historical results or measure the new
rubric's editorial accuracy. No Cloudflare website deployment occurred.
