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
3. Ask pinned Jev 1.13 five independent relevance/substance questions. Preserve
   bounded-context coverage and treat failures as unfinished work.
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

## Bounds and state

The desired schedule is hourly at minute 15 UTC. At most 12 candidates are
considered in a run, one writing investigation runs per invocation, and two
writing attempts are allowed per UTC day. A failed writing attempt consumes
that day's budget; unchanged unfinished candidates retry after 24 hours.
The SDK writer is limited to 16 turns, 24 public source requests and a $2 budget.
Selection thresholds and the exact public-interest rubric live in RUBRIC.md.

Cloud capabilities did not advertise kvStore on September 19. Durable run state
therefore lives in the dedicated notebook-field-notes-state branch of the
private enyst/automations repository, in state.json. GitHub's content SHA is the
compare-and-swap guard for the lease and state updates. It does not modify main.
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
Its runtime bundle is a seven-file allowlist; tests, environments and
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


## Live validation · September 19, 2026

The hourly definition was enabled and read back, with keep-alive disabled and
the normal two-attempt budget restored. The observed Cloud model field retains
its account-assigned evaluation profile; this custom runner intentionally uses
the account defaults instead, as documented above.

Classifier-only run ca196acd-d47c-4bd1-a0bc-fe34c47c88c5 completed without
publication. Two subsequent trials failed model authentication before generating
a note: Cloud had inherited a named evaluation profile. A minimal default-model
completion passed; the runner now explicitly calls get_llm(profile_name=None).
The failed attempts remain in the private audit state. One additional setup
attempt was explicitly staged with a limit of three, then the limit was restored.

Run 73517770-eb45-42a8-bdd1-ce9ec0e0aae8 completed the real chain: eight subjects
considered, two classified, one investigation written and published. The note
about SDK PR #5164 was committed to enyst/enyst.github.io at
30205f565f0a6171341465234cb6c30190d4bb79. Its artifact hash and four key pinned
citations were independently checked. No runtime or test execution is claimed
by the generated note.

The final suite passed 138 tests plus 18 pure classifier/artifact tests, including
the real pinned SDK with mocked model/HTTP transport. The Cloud export's seven
runtime files match maintained source byte-for-byte. Liberty Labs imported one
new record, preserved all 95 existing records and 13 private drafts, and a
scheduled repeat import inserted zero. The local reader was visually checked
at desktop and phone widths. No Cloudflare website deployment occurred.
