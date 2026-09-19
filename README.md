# OpenHands Automations

Private configuration and source history for Engel's OpenHands Automations.
Repository: `enyst/automations`, branch `main`.

## Directory ownership

| Directory | Purpose | Direction |
| --- | --- | --- |
| `local-automations/` | Local backend definitions: one directory per automation, containing `automation.yaml` and extracted code in `tarball/`. | Native OpenHands Git Sync, manually triggered and bidirectional. |
| `cloud-automations/` | Dated exports of the `enyst` OpenHands Cloud account's definitions and available execution sources. | Cloud to Git only during this initial export phase. |
| `sources/` | Editable source and runtime configuration for automations maintained here. | Deploy deliberately with the corresponding helper. |
| `definitions/` | Deployment definitions for maintained automations. | Explicit apply; Git pushes alone do not deploy. |
| `scripts/` | Export, validation, and dedicated deployment tools. | Read each tool's contract before running it. |

Keep these directories separate. A Cloud export is evidence of observed configuration;
editing or pushing it does not apply changes to Cloud. No general Cloud import
or native Cloud Git Sync workflow is enabled. Jev Fast Audit has its own explicit
deployment helper, described below.

## Snapshot index · September 19, 2026

**15 definitions: 11 enabled and 4 disabled — ten Cloud and five local.**
Local sync exported all five with source. The
[Cloud manifest](cloud-automations/manifest.json) records the latest export's
timestamps, completeness, and bundle hashes. Enabled means eligible for triggers,
not currently running.

| Where | Automation | State | Purpose | Export |
| --- | --- | --- | --- | --- |
| Local | [Mention Gazette](local-automations/mention-gazette/) | Enabled | Builds and publishes a digest of unread GitHub notifications; no LLM. | Source complete |
| Local | [Weekly test sweep + simplify (agent-sdk)](local-automations/weekly-test-sweep-simplify-agent-sdk/) | Enabled | Tests and conservatively simplifies the SDK, then may open one PR. | Source complete |
| Local | [Weekly test sweep + simplify (odie)](local-automations/weekly-test-sweep-simplify-odie/) | Enabled | Runs the same bounded test and cleanup procedure for OpenHands Canvas. | Source complete |
| Local | [OpenHands-Astra: supervised review trial 2026-09-17](local-automations/openhands-astra-supervised-review-trial-2026-09-17/) | Disabled | First manual review trial; stopped before publication with zero posts. | Source complete |
| Local | [OpenHands-Astra: supervised review trial 2026-09-17 r2](local-automations/openhands-astra-supervised-review-trial-2026-09-17-r2/) | Disabled | Completed four manual reviews; one later received a transparent correction. | Source complete |
| Cloud | [Weekly re-vendor: smolpaws openhands-agent-server (SDK + server ports)](cloud-automations/automation-d4f8b4de-be10-4795-822b-72ca38dc323e/) | Enabled | Re-vendors SDK main into the SmolPaws server, handles server changes and opens/resumes a PR. | [Source complete](cloud-automations/automation-d4f8b4de-be10-4795-822b-72ca38dc323e/export-status.json) |
| Cloud | [Weekly upstream drift: openhands-agent (OpenHands SDK -> TypeScript)](cloud-automations/automation-e6b527a2-5cd1-45d6-82c5-ac1cf89ebda6/) | Enabled | Ports a bounded Python SDK release interval into the TypeScript SDK and opens/resumes a PR. | [Source complete](cloud-automations/automation-e6b527a2-5cd1-45d6-82c5-ac1cf89ebda6/export-status.json) |
| Cloud | [QA Changes - Auto-Test PR description (software-agent-sdk)](cloud-automations/automation-1dd76c1d-5c79-4598-b19e-018ad893c6ee/) | Enabled | Tests SDK PR changes and appends an Auto-Test report to the PR description. | [Source complete](cloud-automations/automation-1dd76c1d-5c79-4598-b19e-018ad893c6ee/export-status.json) |
| Cloud | [Attention router - weekly SDK PRs and @enyst mentions](cloud-automations/automation-fe2c8185-1b7f-41bf-a687-143e350408b6/) | Enabled | Scores SDK PRs and recent @enyst mentions weekly, then updates Review Notebook notes. | [Source complete](cloud-automations/automation-fe2c8185-1b7f-41bf-a687-143e350408b6/export-status.json) |
| Cloud | [Issue Duplicate Checker - auto-close sweep](cloud-automations/automation-7b7ca607-3052-476e-b9f9-63d98ed98971/) | Enabled | Revisits marked duplicate issues for possible closure; deployed safeguards remain unverified. | [Source complete](cloud-automations/automation-7b7ca607-3052-476e-b9f9-63d98ed98971/export-status.json) |
| Cloud | [Issue Duplicate Checker - detect](cloud-automations/automation-d0f69df6-4757-4403-8106-2013ae5e0db5/) | Enabled | Checks newly opened issues for duplicates and marks candidates for later closure. | [Source complete](cloud-automations/automation-d0f69df6-4757-4403-8106-2013ae5e0db5/export-status.json) |
| Cloud | [Notebook Field Notes](sources/notebook-field-notes/) | Enabled | Selects and publishes public autonomous design investigations across three OpenHands repositories. | [Cloud export](cloud-automations/automation-623cc664-07c2-425e-bc99-8da3d43c4206/) |
| Cloud | [Jev Fast Audit](sources/jev-fast-audit/) | Enabled | Polls four OpenHands repositories hourly and replaces its scorecard in eligible PR descriptions. | [Deployment definition](definitions/jev-fast-audit.json); [export manifest](cloud-automations/manifest.json) |
| Cloud | [Roasted Code Review - OpenHands PRs (on behalf of @enyst)](cloud-automations/automation-de2d1215-bbb3-42a9-bec4-7feee4f19a86/) | Disabled | Historical PR reviewer posting COMMENT reviews and updating the public review log. | [Source complete](cloud-automations/automation-de2d1215-bbb3-42a9-bec4-7feee4f19a86/export-status.json) |
| Cloud | [Daily external PR security screen and review for OpenHands repos](cloud-automations/automation-e2ca316e-2896-4189-b1fc-fef6100d85f1/) | Disabled | Screens external contributors’ PR diffs for security concerns and reports findings. | [Source complete](cloud-automations/automation-e2ca316e-2896-4189-b1fc-fef6100d85f1/export-status.json) |

**Download workaround:** an em dash (`U+2014`) in five Cloud names broke Latin-1
encoding of the download filename header, producing HTTP 500. Replacing those
name characters with ASCII hyphens restored direct downloads for all eight.
This is a naming workaround, **not a server patch**; the upstream report is
[OpenHands/automation #498](https://github.com/OpenHands/automation/issues/498).
The initial partial export and Attention archive-recovery receipt remain in Git
history; the current snapshot uses direct Cloud downloads throughout.

The deleted TypeScript-client release maintainer is retired history and is not
included in these fifteen definitions.

## Jev Fast Audit

Cloud automation **`5aee9a93-51e5-4843-ad13-301a31e1397e`** runs the source in
[`sources/jev-fast-audit/`](sources/jev-fast-audit/). Deployment read-back verified
**enabled, hourly** (`0 * * * *`, UTC) on September 19, 2026, polling:

- `OpenHands/OpenHands`
- `OpenHands/software-agent-sdk`
- `OpenHands/automation`
- `OpenHands/extensions`

Polling considers PRs updated from **2026-09-18 22:41:32 UTC** onward.
The deployed configuration has no manual target, force re-auditing is off,
the run timeout is 240 seconds, and sandbox keep-alive is off.

The runner calls TypeSafe's **`jev-1.13.0`** classifier and writes as **@enyst**.
It reads the Cloud integration's built-in `github_token`; the custom Cloud
`ENYST_GH_TOKEN` copy was removed after a successful migration trial on September
19. The existing `REMOTE_GH` secret is preserved.
It maintains one `## Jev-Fast-Audit` section in each selected PR description:
a compact signal, evidence link, coverage, and timing, with all estimates in a
collapsed table. Repeat runs replace that section; unchanged inputs with a valid
signed receipt skip another classifier call. Polling skips drafts and closed PRs,
uses the configured activation-time floor, and processes at most six changed PRs
per run by default. These are estimated signals, not review approvals.

The [SDK security rubric](sources/jev-fast-audit/SECURITY_RUBRIC.md) maps nine
security checks to pinned SDK system-prompt instructions. The current request
uses 15 probability questions, 15 evidence selections, and one primary-concern
choice. Full question wording is retained. There is no impact score, test-coverage,
description-mismatch, resource-cleanup, or general behavior-regression question.
Jev's token limits are enforced by the provider; byte counts are transport metrics.

The initial manual trial verified replacement on repeat runs and classifier calls below
0.5 seconds. That timing measures the classifier call, not the entire automation.
See the [implementation notes](sources/jev-fast-audit/README.md) for coverage
limits, polling behavior, credential references, and the PR-description editing
race limitation.

The maintained deployment definition is
[`definitions/jev-fast-audit.json`](definitions/jev-fast-audit.json).
Apply source or configuration changes explicitly using
[`scripts/deploy_jev.py`](scripts/deploy_jev.py), after checking the definition's
schedule and bundled configuration. The helper accepts public or private
`enyst/automations`, verifies the `enyst` GitHub identity and exact repository,
and reports current visibility in preflight. It does not change visibility:

```sh
python3 -B scripts/deploy_jev.py preflight
python3 -B scripts/deploy_jev.py deploy \
  --definition definitions/jev-fast-audit.json \
  --automation-id 5aee9a93-51e5-4843-ad13-301a31e1397e
python3 -B scripts/deploy_jev.py status \
  --automation-id 5aee9a93-51e5-4843-ad13-301a31e1397e
```

The helper uploads the bundle and applies the definition through the Cloud API;
updates preserve enabled state unless explicitly paused. Verify live settings
and runs, then refresh the Cloud export. **Pushing this repository does not apply
Cloud changes.** Exported files remain observations and backups; local native
Git Sync continues to use only `local-automations/`.

## Planned automations

- [Jev release security triage](plans/jev-release-security.md) — planned September
  19, 2026, starting with SDK release PRs. Move the current scan into OpenHands
  Cloud, retain exact integrity/dependency checks, and add Jev judgments for
  suspicious content. Publish a concise ✅/❌ comment requiring human inspection
  whenever evidence is concerning, incomplete, or stale. **Not deployed**; the
  existing release scan remains active.

## Local Git Sync

**Configuration verified September 18, 2026:** SSH remote
`git@github.com:enyst/automations.git`, branch `main`, path `local-automations`,
`interval_seconds: 0`, no stored Git token, and no content encryption.
The first native sync completed at **2026-09-18 01:49:00 UTC**, producing
commit [`1636b1c`](https://github.com/enyst/automations/commit/1636b1c0a3fbae55f7f9e1d230e67bfb886664b1):
**5 definitions and 49 bundled source files** (54 files including metadata). Exported content hashes matched the preflight
archives, and enabled states were preserved: **3 enabled, 2 disabled**.

A native sync cycle is **pull → import → export → push**. It is not an export-only
backup: edits to local `automation.yaml` or `tarball/` files can update live local
definitions on the next manual sync. Newly added definitions can be imported too.
Changes made through the service take precedence when that same definition has
unexported service changes.

1. Check the local Git Sync status and review repository changes.
2. Make intentional edits under `local-automations/`, including any enabled-state
   or trigger changes; commit and push to `main`.
3. Trigger one manual native sync and inspect its resulting commit, status and errors.
4. Read back affected live definitions before considering the change applied.

The service exposes `GET /api/automation/v1/git-sync/status` and
`POST /api/automation/v1/git-sync/sync`. An accepted trigger is not proof that the
cycle finished. Manual Git sync does not disable automation schedules: imported
enabled definitions can respond to their configured triggers.

## Cloud exports and runtime status

Export Cloud definitions and available source before designing any import/apply
workflow. `scripts/export_cloud.py` requires `--expected-org-id` and can also check
`--expected-user-id`; verify the target account before collecting. It does not
commit, push or change Cloud definitions.

From this repository, using the `OPENHANDS_API_KEY` entry under Keychain service
`openhands` without displaying its value:

```sh
python3 scripts/export_cloud.py \
  --expected-org-id 18881862-24a9-4521-ba8a-8424314c6458 \
  --expected-user-id 18881862-24a9-4521-ba8a-8424314c6458
```

Exit codes: **0** complete export; **2** partial export with incomplete entries
recorded; **1** failed identity, safety, credential or export check. Consult the latest
[Cloud manifest](cloud-automations/manifest.json) for current completeness. The
September 18 refresh returned **0** with its eight definitions complete; the
initial partial export returned **2** before the naming workaround.
An optional `--recovery-manifest` accepts an approved private recovery record for
an exact saved archive; do not substitute an arbitrary source checkout. A refresh
without that recovery can report a bundle unavailable while preserving its
previously verified files.

Each complete export uses `automation-<UUID>/automation.yaml` and `tarball/`.
`manifest.json` records origin IDs, hashes, timestamps and completeness. Missing
bundles are recorded as incomplete, without an importable `automation.yaml`; an
existing complete export is preserved if a refresh fails. Recovery from a local
archive requires matching provenance and hashes. A metadata-only export is not a
complete restore package. Preserve identity and disabled state when comparing
exports.

Export timestamps, enabled flags and last-run results are observations, not live
monitoring. Check the relevant backend for current state. A completed run alone
does not verify its external effects.

## Credentials

Git transport uses the host's SSH authentication. OpenHands API credentials remain
in the runtime or macOS Keychain, including the `openhands` service; repository
files may name credential references but must not contain values.

No content encryption layer is configured for these backups. Keep API keys, tokens,
private keys, secret files and credential-bearing URLs out of definitions, bundled
code, exports and Git history. Repository access can permit executable local
configuration changes through the next sync.


## Notebook Field Notes

[Maintained source](sources/notebook-field-notes/) and the
[deployment definition](definitions/notebook-field-notes.json) implement public,
autonomous design investigations for Liberty Labs Notebook. They watch public
PRs and issues across OpenHands, software-agent-sdk and automation.

Deterministic gates run before `jev-1.13.0` answers six independent questions:
design, agent behavior/performance, memory, cross-repository interaction,
explanatory substance and sufficient design context. Classifier policy 2 uses
only PR/issue titles and descriptions plus up to four linked issue descriptions;
it sends no diffs, fetched source or discussion comments. The classifier state
has a **24,000-byte UTF-8 transport bound**, not a model token limit. Policy 2
invalidates cached decisions from the earlier classifier policy.

Insufficient design context is its own bucket, independent of topic interest.
Only complete, untruncated input can lead to a fixed request for more context:
at most one automatic comment attempt per open, nondraft PR and two attempts
per UTC day. Missing retrieval or truncated input defers without an author-facing
comment. Descriptions, including linked issue improvements, can be assessed
again after the 24-hour cooldown; an existing request is not posted again.

After selection, a bounded OpenHands writer still reads pinned public code;
trusted code validates and creates a dated note in
enyst/enyst.github.io/field-notes. Notes carry their autonomous byline and
evidence. Existing notes are never overwritten by this automation.

The desired hourly schedule allows at most two writing attempts per UTC day.
The comment counter is separate, but exhausting the writing allowance still
stops the whole run, including discovery and clarification checks, until a later
UTC day. There is no persistent pending queue:
unprocessed items are rediscovered oldest-update-first within the rolling
seven-day window and can age out. The six probabilities are selection gates,
not a score-based ranking.
Its state and lease use a dedicated branch of this repository, because
Cloud did not advertise the KV capability when this was deployed. Public and
private repository visibility are both supported; GitHub content SHAs protect
concurrent state updates.
Liberty Labs imports new notes into local D1 separately and preserves all
existing writing, visibility decisions and plate numbers. Website deployment
remains a separate action.

Use scripts/deploy_field_notes.py to stage, test, enable, inspect or pause this
automation. The nine-file runtime allowlist includes `descriptions.py` and
`comments.py`; tests, local environments and working references are excluded.
After an explicit deployment, scripts/export_cloud.py refreshes the
observed definition and exact runtime source under cloud-automations/.
Pushing this repository alone does not update Cloud.
