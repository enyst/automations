# Cloud source reconciliation · October 9, 2026

## Result

All 13 currently deployed Cloud automations were downloaded and checked. Their
runtime files are now the canonical repository copies. IDs, schedules, activation
states, model settings, and code in Cloud were left unchanged.

The export includes newer Cloud changes in Notebook Field Notes, the Attention
router, both weekly porting jobs, and two definitions missing from the older
repository snapshot. Cloud Field Notes uses policy 3: five classifier questions,
with implementation readiness separate from research interest. Its note budget
and information-request budget are also separate. The old repository policy was 2.

Repository-only documentation moved to `docs/`. Field Notes policy tests moved to
`tests/test_field_notes_core.py`. The unconfigured SDK verifier settings moved to
`examples/sdk-lifecycle-conformance.json`; the deployed trial configuration stays
in its Cloud bundle. Historical evaluation evidence remains unchanged. Older
runtime versions remain in [commit 50ed9d4](https://github.com/enyst/automations/tree/50ed9d45c9b485b632a808a796357834c2c0c779).

The [verification receipt](cloud-reconciliation-check.json) records the two fresh
downloads. All 67 runtime files match, and all 13 IDs and settings are unchanged.
The regression suite passed 233 tests; four optional SDK integration tests were
skipped because the SDK was unavailable in the test environment. Runtime bundles
and publication files passed the credential-pattern scan.

## Native Git Sync: blocked at credential storage

The intended destination is `enyst/automations`, branch `main`, path
`cloud-automations/`. Cloud's Git Sync was disabled before this work.

The setup used the existing Cloud `REMOTE_GH` secret in process memory. The
repository access check passed and found the staging branch. That check proves
read access; it does not prove a successful push. Saving the configuration with a
token returned HTTP 503. A non-secret placeholder token produced the same error.
Saving `enabled: false` without a token succeeded. Read-back confirmed that sync
was disabled and no repository or sync commit had been recorded.

The gateway returned a generic HTML error, not the underlying service error.
The inspected service code returns 503 when it cannot encrypt stored Git Sync
credentials. A missing server `AUTOMATION_GIT_SYNC_SECRET` (or the supported
`AUTOMATION_KV_SECRET` fallback) is a plausible cause, not a confirmed diagnosis.
A Cloud operator must check the logs and credential-storage configuration. An
account-level custom secret cannot configure that service-level encryption key.

The temporary setup sandbox was paused. No general sync cycle ran. No Cloud
runtime, schedule, or enabled state was changed. Local Git Sync was not changed.

## Finish after Cloud credential storage works

1. Save a new complete Cloud export outside the sync directory. Verify current
   IDs, runtime file hashes, settings, and repository changes before proceeding.
2. Use a temporary branch with an empty `cloud-automations/` directory. Do not
   point a first native sync at these UUID-named export folders: native sync
   matches its own slugs and could create duplicate automations.
3. Configure that branch for manual sync with the existing Cloud token. Ensure
   each existing automation is queued for export before the first import step.
   Inspect the deployed service behavior; missing clean folders can mean deletion.
4. Run one sync. Compare the full Cloud inventory and source hashes with the
   saved baseline. Check that nothing was created, deleted, enabled, or disabled.
5. Replace the UUID export layout with the service-generated directories. Update
   tests and helper paths, and review the resulting PR against `main`.
6. After that baseline is merged, configure branch `main`. Test a bounded Git-side
   edit on a disabled automation and verify its read-back before relying on
   deployment from Git. Choose a nonzero interval for automatic synchronization;
   zero means manual only. Record the verified configuration and commit.

Native sync owns `automation.yaml` and `tarball/`. Do not rename native slug
folders casually: that can mean deletion and creation. Cloud edits queued for
export win a simultaneous Git edit in that cycle. Refresh and compare before
editing so a newer deployment is not replaced by an older repository version.

## Refresh the current export

Until native sync is active, the existing GET-only exporter can refresh the
baseline. Commit or branch repository edits first. It does not deploy changes.

```sh
python3 -B scripts/export_cloud.py \
  --expected-org-id 18881862-24a9-4521-ba8a-8424314c6458 \
  --expected-user-id 18881862-24a9-4521-ba8a-8424314c6458
```

Once native sync owns this path, export to a separate temporary directory for
comparisons. Do not write the UUID layout over native sync's directory tree.
