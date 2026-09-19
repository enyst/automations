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
3. Ask pinned Jev `jev-1.13.0` six independent questions using only the PR/issue
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

## Description triage · policy 2

The six questions cover design, agent behavior/performance, memory,
cross-repository interaction, explanatory substance and sufficient design
context. The context question is independent of interest: a vague description
must not disappear as "uninteresting" because its purpose cannot be understood.
A clear explanation of a small routine fix can be sufficient; architecture
documents are not required for every change.

With complete, untruncated input, design context at or below 0.30 enters the
**needs-information** bucket; values between 0.30 and 0.70 defer. With context
at least 0.70, writing requires at least one topic probability at least 0.65 and
substance at least 0.70. Otherwise the clearly described topic is skipped.
These are independent estimated probabilities, not an overall score or a
ranking. Exact questions and examples live in [RUBRIC.md](RUBRIC.md).

A needs-information result may produce one fixed, transparently attributed
comment asking for a brief explanation of the problem, affected components and
intended behavior. Comments are limited to **one automatic attempt per open,
nondraft PR**, and **two comment attempts per UTC day**, separately from the
writing budget. The existing whole-run daily cap still stops discovery and
comments once the writing allowance is exhausted; they resume on a later UTC
day. Issues and closed or merged PRs do not receive these comments.
The script checks current descriptions and the source revision again before
posting. It persists a reservation first, and never repeats an uncertain POST.

Failed or incomplete retrieval and truncated input always **defer**, even when
Jev's probabilities are high. They cannot trigger a request blaming an author
for missing context. Descriptions can be evaluated again after the 24-hour
cooldown, including improvements in linked issue descriptions; this does not
authorize another comment on a PR already asked. Classifier policy version 2
invalidates old decision-cache entries. Fully delimited Jev Fast Audit
scorecards are excluded from classifier input to avoid feeding prior scores
back into the model, while original descriptions remain intact.

## Bounds and state

The desired schedule is hourly at minute 15 UTC. At most 12 candidates are
considered in a run, one writing investigation runs per invocation, and two
writing attempts are allowed per UTC day. A failed investigation after budget
reservation consumes that day's allowance; technical writer initialization
checks run before reservation. Unchanged unfinished candidates retry after
24 hours.
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
status and timestamps, daily counters, comment receipts and an expiring lease;
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
