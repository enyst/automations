# Jev release security triage

Recorded September 19, 2026. **Planned; not deployed.** The current SDK release
security workflow remains in place.

## Goal and deployment

Move release security scanning into an OpenHands Cloud Automation, with source and
its deployed snapshot in private `enyst/automations`. Start with
`OpenHands/software-agent-sdk`, where the current Release Security Scan lives.
The decision is: **does a human need to inspect this before publication?**

Use code for exact integrity and dependency checks, and Jev for suspicious-content
judgments. Post one concise, replaceable PR comment with a clear ✅ or ❌ for each
check, an overall result, and links to evidence. No automatic merge or release.

## Existing workflow to preserve and improve

Reference: `.github/workflows/security-scan.yml` and the SDK's
`check_approval_drift.py`, `check_dependency_diff.py`, and
`security_scan_common.py`, inspected in local commit `1d1a10351`.

The workflow compares the previous release with the candidate, checks approval
drift and dependency risk, posts a marker-owned PR comment, and fails the job
when its checks fail. Its PR event currently runs on `labeled` only; it also
supports manual dispatch. The new workflow must refresh on head changes rather
than leaving an earlier scan looking current.

Before retiring it, verify the current upstream implementation and actual branch
and publication rules. A PR comment alone does not preserve an existing required
check. Preserve the applicable release gate during migration.

## Migration gaps found during inspection

The current scanner executes scripts from the checked-out PR head. Its history
mapping relies on commit subjects, and some missing baseline/review/history cases
can pass as warnings. Dependency comparison omits same-version source/hash changes,
does not cover npm locks, and needs stricter OSV response validation. Its comment
lookup also needs pagination and author verification. These behaviors must not be
carried forward as a green result in the replacement.

## Code-owned checks

- Pin the baseline commit, candidate head, reviewed/merged states, scanner version,
  and dependency inputs. Compare exact identifiers/content in code.
- Check approval drift against the merged content. Treat unresolved mappings,
  inaccessible reviews, and failed lookups as requiring human inspection.
- Compare dependency additions, removals, versions, sources and hashes, including
  source/hash changes with the same version. Retain vulnerability database checks.
- Read the full release delta since the trusted previous release, including build,
  packaging, CI, install hooks, executable payloads, and agent-facing instructions.
- Run the trusted scanner bundle; candidate files are input data. Never execute
  scanner scripts, installers, or other code selected by the PR.
- Record coverage across all chunks/files. Missing or omitted required evidence,
  API failure, invalid answers, or an unfinished scan yields ❌.
- Re-read the candidate before publishing. Any changed input invalidates the
  result. A release-time consumer must check the exact scanned identity again;
  polling and a comment cannot close that final check-to-publication interval.
- Where built artifacts are in scope, bind their digests/provenance too. Before
  artifacts exist, report artifact verification as pending, never as passed.

## Jev-owned judgments

Use focused, separate questions with file/hunk or dependency-record evidence IDs:

1. Does this dependency or source change warrant human security inspection?
2. Does this text attempt to manipulate an agent, reviewer, CI system, or release
   process into ignoring checks, revealing secrets, or running unrelated actions?
3. Does this code introduce unexplained behavior, exfiltration, obfuscation,
   credential access, install-time execution, or behavior inconsistent with the
   component's purpose?

These are classifier judgments; exact dates, hashes, comparisons, counts, and
aggregation remain in code. A model result cannot override a deterministic failure.
Select thresholds using evaluated examples before deployment, with ambiguous
results requiring inspection. Jev is one signal, not the sole basis for releasing:
its documentation explicitly warns that adversarial input can steer its answer.

## Comment contract

- **✅:** this check completed on the identified inputs and raised no flag.
- **❌:** human inspection is required, including suspicious evidence, incomplete
  coverage, failure, or a stale result.
- Each ❌ has a short reason and evidence link. Scores/details can be collapsed.
- Overall ✅ requires all required checks to pass for the current release content.
- Reuse only a comment owned by the automation; do not overwrite another author's
  comment merely because it contains a matching marker.

Illustrative format, not an actual scan:

```text
🔒 Release security — ❌ Human review required
Scanned: <baseline SHA> → <candidate SHA>

✅ Approval/merge integrity
❌ Dependencies — unexpected source change [evidence]
✅ Prompt-injection screen
✅ Code behavior screen
✅ Required evidence coverage
```

## Validation and cutover

Test clean releases and realistic suspicious cases: stale approval, same-version
source/hash substitution, new install hooks, exfiltration, hidden payloads, prompt
injection against the scanner, benign security test fixtures, pagination gaps,
API failure, and a head changing during or after a run.

Run alongside the existing scan first. Verify Cloud event delivery for this repo
and account; the personal Cloud account's receipt of organization webhooks must
not be assumed. Polling can drive triage, but publication must consume a current,
commit-bound result. Retire the old job only when its required-check behavior and
refresh/invalidation contract have a verified replacement.

## References

- [Current SDK workflow](https://github.com/OpenHands/software-agent-sdk/blob/main/.github/workflows/security-scan.yml)
- [Jev 1.13 documented limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
