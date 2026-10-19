# Monday SDK → Wednesday server handoff

Updated October 19, 2026. The existing Wednesday automation is
`d4f8b4de-be10-4795-822b-72ca38dc323e`. Its maintained runtime is
`cloud-automations/automation-d4f8b4de-be10-4795-822b-72ca38dc323e/tarball/`.

Wednesday selects the sole open `drift/` PR targeting
`smolpaws/openhands-agent` main, including drafts, authored by `smolpaws` or
`enyst`, with a head in the canonical repository or `enyst/openhands-agent`.
It fetches the canonical `refs/pull/<number>/head` ref and verifies its exact
commit. With no such candidates it pins canonical SDK main. Multiple,
untrusted, malformed or changing candidates stop the run; API errors do not
fall back to an agent-selected revision.

The no-work comparison uses the vendored package's
`_smolpawsProvenance.gitCommit`, not the Python compatibility pin. A TypeScript
change therefore triggers re-vendoring even when both Python pins match.
An existing trusted canonical `revendor/` server PR is resumed and brought to
the selected SDK revision, invalidating evidence for its previous dependency.

The runner rechecks the selection before model startup. The task requires
another check before vendoring and each push/publication, full SDK and server
validation, and an explicit SDK PR URL and SHA in the downstream PR. That PR
stays a draft while its SDK dependency is unmerged. No merges or pushes to main
are authorized. Monday's runtime and both schedules are unchanged.

## Validation

- 22 focused behavioral tests, including execution of the packaged runner with
  synthetic workspace/SDK modules, API pagination and failures, stale selection,
  draft/fork trust, resume handling, and inherited mode overrides.
- A real temporary Git fixture executes the emitted checkout commands and
  verifies that moved or missing pull refs stop before checkout.
- Full automation suite: 361 tests, 4 skipped, successful.
- Packaged Python compilation, setup shell syntax and conformance deployment
  dry run passed. Companion SmolPaws documentation passed root type-checking.
- A read-only live selection found SDK PR
  [#64](https://github.com/smolpaws/openhands-agent/pull/64) at
  `a66bdef0d3099fc46fa0ab9b20f4d3989247dfde`. This is a dated observation,
  not a hardcoded dependency for future runs.

## Deployment record

At 2026-10-19 02:47 UTC, the existing job was updated with the tested bundle
and prompt. A fresh Cloud download matched all seven source files byte for
byte, including executable modes. Downloaded archive SHA-256:
`39b128f1f9ac7eca85f9abe537879bfcfc52d33fb38c1a1a062cc82ae401bdc3`.

The job remains enabled, Wednesday at 06:00 UTC, with the same model, profile,
30-minute timeout, setup script and entrypoint. Both prompt metadata fields
match the deployed `prompt.txt`. No run was dispatched; the live check only
selected the SDK dependency. Monday was not modified.

The affected definition, export receipt and manifest row were refreshed from
that download. The manifest's original inventory date remains historical;
`partial_refreshes` records this one-job refresh. Other jobs were not
re-exported. Original and new bundles were retained privately for rollback.
Git changes alone still do not deploy this Cloud job.
