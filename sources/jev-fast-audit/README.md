# Jev Fast Audit

Implementation notes, September 19, 2026. This page describes the code and intended
operating configuration; check the Cloud definition and run receipts for live state.

Jev Fast Audit adds a compact scorecard to a pull request description. It calls
TypeSafe's classifier directly through `POST https://api.typesafe.ai/v1/systemone`,
pinned to **`jev-1.13.0`**. OpenHands supplies scheduling and a sandbox for the Python
script. The script starts no LLM conversation, escalation, or second review.

## Output and ownership

The script writes as **@enyst**, after verifying the GitHub credential's identity.
Its only GitHub write is a PR description update containing this owned block:

```markdown
<!-- jev-fast-audit:start -->
## Jev-Fast-Audit

<compact scorecard and collapsible estimates>
<!-- jev-fast-audit:end -->
```

A subsequent run replaces this block. Duplicate owned blocks are consolidated;
existing unmarked `## Jev-Fast-Audit` sections are recognized through the next
level-one or level-two heading. Headings inside fenced examples are ignored.
Malformed ownership markers or an unclosed code fence stop the update.
Within the fetched description, other content is preserved, including sections
written by humans or other automations. Concurrent editing has the limitation below.

The visible summary shows the strongest signal, an evidence link, coverage,
classifier latency, and the reviewed commit. A collapsed table contains fifteen risk
estimates and the selected primary concern. Noul values
are model estimates of likelihood. They are not approvals or calibrated guarantees.
The runner does not submit reviews, post comments, merge changes, or run PR tests.

GitHub does not offer an atomic compare-and-swap operation for PR description
PATCHes. The runner reads the PR again immediately before writing, checks for
intervening edits, and verifies the result afterward. These checks reduce races
with another editor; they cannot eliminate the final read-to-write race. An edit
arriving in that narrow window can still be overwritten without detection. The
runner does not claim atomic preservation against concurrent writers.

## Evidence and coverage

1. Read the complete paginated changed-file listing and compare its count with the
   PR's `changed_files` value. An incomplete listing aborts the audit.
2. Use the GitHub patch for each changed file. Parse whole hunks, check their line
   counts, and compare additions/deletions with GitHub's file totals.
3. Resolve the merge base and read source at immutable merge-base/head commits.
   Include up to 20 surrounding lines before and after each hunk on both sides.
   Source fetching is bounded to the first 40 files with patches; files above
   250,000 bytes, missing content, and binary content may lack surrounding context.
   Network failures, rate limits, authorization failures, and other API errors
   abort that PR's audit before a new scorecard or signed receipt is written;
   a later poll can retry it. HTTP 404 and oversized responses are treated as
   unavailable source and are reported as missing context.
4. Assign file IDs such as `F001` and hunk IDs such as `F001H001`. Each risk has an
   independent evidence-selection question whose choices are included hunk IDs or
   `NONE`. Code maps the selected ID to a commit-pinned GitHub file/line link;
   deleted code links to the merge-base version.

Jev's published limits are **64k tokens for state plus all questions**, and
**32k tokens for state plus the longest question**. Questions are evaluated in
parallel against the shared state. Full instructions and criteria are preserved
for every question; the previous 30 KiB request cap confused bytes with context
capacity and has been removed. See [the model limits](https://docs.typesafe.ai/models).

No official Jev tokenizer or preflight counting endpoint is published. The
provider validates token fit; actual token usage is retained from its response.
Local byte counts are transport measurements, not token estimates. An oversized
request rejected by the API fails that audit without publishing or caching a
scorecard; the runner does not silently shorten its questions or retry with a
weaker rubric.

A separate **1 MiB operational transport guard** bounds local payload handling.
Within that guard the builder admits whole patch hunks before adding surrounding
source, so early import context cannot displace later code/test changes. It never
cuts a hunk mid-code. Omitted hunks/files, missing or budget-omitted context,
malformed/incomplete patches, and oversized description/title fields are
recorded explicitly. Oversized text fields are omitted whole, with their byte
count and hash retained. If essential metadata cannot fit, the audit fails.

Every scorecard reports complete supplied coverage or **partial coverage**, with
file/hunk counts and omission reasons. Complete supplied coverage means the
selected patch and surrounding context were available; it does not mean the
whole repository was analyzed. Missing evidence is not treated as proof of safety.
The runner reads source as data and never executes code, tests, or installation
commands from the PR.

## Question design and validation

Each risk asks whether at least one concrete introduced operation fits its claim.
Both answers have worked examples, including legitimate uses and defensive tests.
The full common security instructions and original claims remain in every rubric;
independent hunk and primary-concern Choices are unchanged. This adopts the useful
question-writing pattern from [Abide's compiler guidance](https://github.com/coldteadotai/abide/blob/5099cbb1020f5885181f0fa9eb2851cb8f1e93ce/skills/abide-compile/SKILL.md).
It remains fifteen Nouls, fifteen evidence Choices, and one headline Choice, with
no impact score, automatic approval, or LLM escalation.

The September 19 comparison used twelve independently prepared synthetic cases
and the unchanged trial PR. The candidate separated several positive/benign pairs
more clearly, but still did not flag the original PR helper. This is a small
exploratory test, not calibrated security accuracy. Results and limitations live
in `evaluations/abide-comparison/` in the private backup repository.

All returned probabilities must be finite and within [0, 1], with exactly the
expected keys. Choice distributions allow at most 0.01 rounding error in their
sum: actual Jev replies sometimes total 0.99. Values are retained unchanged, not
renormalized; larger errors stop publication. A tiny numeric tolerance handles
floating-point representation at that boundary.

## SDK-derived security questions

Nine additional rubrics come from the SDK's default security, risk-assessment,
repository-context, memory, and secret-handling instructions. They cover sensitive
data disclosure, unexpected transfers, credential misuse, prompt injection,
package-source redirection, unverified remote execution, privileged environment
access, security-assessment bypass, and prohibited workloads. Each has a full
probability question and an independent file/hunk evidence question.

The [source mapping and interpretation guide](SECURITY_RUBRIC.md) pins the SDK
revision and explains exceptions. A patch can expose risky behavior; it usually
cannot establish private user consent, actual execution, or a tool's risk label.
Scores are evidence-based scrutiny signals, not a claim that every flagged action
violated policy. The remaining six original review categories retain their full wording. Test coverage,
description mismatch, resource cleanup, behavior regression, and impact scoring
are not asked or displayed.

## Repeat runs and scope

The input fingerprint covers head SHA, base SHA, PR title, description with Jev's
section removed, model version, runner version, and the hash of `audit.py`.
A hidden `jev-input-signature` receipt is a domain-separated HMAC of that
fingerprint using the TypeSafe API key. An unchanged, matching signed receipt skips
another classifier call. An author cannot create a valid new receipt merely by
calculating the public fingerprint. This signs input identity, not the displayed
scorecard text. GitHub OAuth token refresh leaves these receipts valid; rotating
the TypeSafe key invalidates them. A signed partial audit caused
by explicit content or size limits remains cached until its inputs change; transient
source-fetch failures do not receive a new receipt.

Target selection supports a GitHub event, an explicit command-line target, a
configured `manual_target`, or polling. Every selected repository must also appear
in the configuration allowlist.

The intended recurring Cloud configuration is **every hour**
(`0 * * * *`, UTC), polling these repositories:

- `OpenHands/OpenHands`
- `OpenHands/software-agent-sdk`
- `OpenHands/automation`
- `OpenHands/extensions`

Set `watch_since` to the chosen UTC activation time so polling starts with PRs
updated from that point onward. It is an update-time floor, not a creation-time
filter: an older PR updated after activation can qualify. Polling skips drafts,
closed PRs, and inputs with a current signed receipt. By default, a run processes
at most six changed PRs. Selection takes turns across repository queues, rotating
both repository order and the starting PR in each queue every time-slot
rotation. Each queue is ordered by PR number, so repeatedly failing PRs cannot keep
occupying its first positions. With four nonempty repository queues and the default
six-PR limit, every repository receives a turn each run; later slots reach PRs
behind repeated failures. Before recurring operation, remove `manual_target` and
set `force_reaudit` to false or remove it.

Cloud supports native GitHub event triggers, but the forwarding service routes
organization-owned repositories to the Cloud organization claiming that owner.
Delivery to an individual member's personal Cloud account is not established by
creating an event filter. Polling makes the personal account's scope explicit.

## Source, deployment, and backup

| Path | Responsibility |
| --- | --- |
| `sources/jev-fast-audit/` | Editable source of truth: runner, rubric/context builder, section editor, and runtime configuration. |
| `scripts/deploy_jev.py` | Explicit upload/create/update, credential installation, dispatch, and status commands. |
| `cloud-automations/automation-<UUID>/` | Export of the observed Cloud definition and its exact deployed bundle. |

Edit source here, deploy deliberately, verify the run, then refresh the Cloud
export. An export is a backup/receipt; editing or pushing its files alone does not
apply changes to Cloud. Native local Git Sync uses `local-automations/` separately.

From the repository root:

```sh
python3 -B scripts/deploy_jev.py preflight
python3 -B scripts/deploy_jev.py install-secrets
```

### Credentials

The helper uses `OPENHANDS_API_KEY` from Keychain service `openhands` for Cloud
administration. Its local GitHub identity and repository-privacy checks use the
local `ENYST_GH_TOKEN`; that token is never uploaded. `install-secrets` installs
only a missing `TYPESAFE_API_KEY`, preserving all existing Cloud values.

The runtime reads the Cloud integration's built-in **`github_token`** and the
custom **`TYPESAFE_API_KEY`** through the sandbox-scoped endpoint:

```text
GET /api/v1/sandboxes/{SANDBOX_ID}/settings/secrets/{name}
X-Session-API-Key: <sandbox session key>
```

The GitHub integration resolves the owner's current OAuth credential and refreshes
it when needed. The runner verifies that GitHub reports `enyst` before writing.
A raw Python automation does not depend on an agent conversation exporting
credentials into its shell. No values belong in configuration, source, archives,
receipts, or Git history.

**Migration verified September 19, 2026:** a Cloud trial resolved `github_token`
as `enyst`, confirmed repository write permissions for all four OpenHands repos,
and successfully replaced the existing audit section on the private test PR.
The redundant custom Cloud `ENYST_GH_TOKEN` was then removed. The existing
`REMOTE_GH` secret and local Keychain entry were preserved. Version 2 changes the
receipt signing key/domain, so prior receipts refresh once; subsequent GitHub
OAuth refreshes do not trigger unnecessary audits.

Supply a definition JSON file with `name: "Jev Fast Audit"`, the intended `trigger`,
`entrypoint: "python3 main.py"`, and optional `timeout`/`keep_alive`. The helper adds
the uploaded bundle reference. It validates identities, repository privacy,
syntax, archive limits, and credential-like content before applying the definition.

```sh
python3 -B scripts/deploy_jev.py deploy \
  --definition /path/to/definition.json \
  --automation-id <existing-automation-uuid>
python3 -B scripts/deploy_jev.py status --automation-id <existing-automation-uuid>
python3 -B scripts/deploy_jev.py dispatch --automation-id <existing-automation-uuid>
```

For first creation, omit `--automation-id`. Updates preserve enabled state unless
`--paused` is specified. A new paused deployment requires the staging trigger
`{"type":"cron","schedule":"0 0 1 1 *","timezone":"UTC"}`; creation is followed
by disabling and read-back verification. A disabled automation cannot be manually
dispatched. Manual dispatch accepts no event body, so its target comes from the
bundled configuration or polling selection.

After deployment and verification, collect the live definition and bundle:

```sh
python3 -B scripts/export_cloud.py \
  --expected-org-id 18881862-24a9-4521-ba8a-8424314c6458 \
  --expected-user-id 18881862-24a9-4521-ba8a-8424314c6458
```

The export command is GET-only and does not commit or push. Review its manifest
and completeness before committing source and exported artifacts to this private
repository.

## References

- [TypeSafe documentation](https://docs.typesafe.ai/introduction)
- [TypeSafe documentation index](https://docs.typesafe.ai/llms.txt)
- [Classifier API](https://docs.typesafe.ai/api)
- [Jev 1.13 limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
