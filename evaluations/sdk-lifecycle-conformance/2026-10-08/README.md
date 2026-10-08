# Initial external lifecycle evaluation — 2026-10-08

These are retained evaluation receipts, not live Cloud execution or activated
merge admission. Each JSON receipt records its own verifier bundle digest;
historical receipts are kept immutable even when the proposed verifier changes.

## Qualification

`reference-positive.json` runs all three required scenarios over actual loopback
HTTP/WebSocket traffic against a small test-only reference protocol server and
the actual scripted HTTP provider. All three pass.

`reference-negative-inversion.json` deliberately reverses the two durable events
committed by a live message. Replay and storage restoration still work, but both
live continuation predicates fail, and the legacy smoke passes. This qualifies
the verifier's distinction between complete successful execution and a real
ordering counterexample. It is not evidence of SDK product behavior.

Eleven automated test groups pass: full-roster positive qualification, deliberate
live inversion, missing/duplicate replay events, wrong REST payloads, non-durable
cursor contamination, unconfigured blocked roster, credential URL rejection,
nested credential redaction, actual HTTP/WS redirect rejection, and rejection of
unrelated provider traffic as this run's nonce witness. An independent
review reran qualification and the credential redaction control.

## Actual SDK candidate

The real Agent Server run uses clean
`OpenHands/software-agent-sdk@9f47d471ee6f91ba1d140d10d34f3b26d5ac2427`, with a
`git archive --format=tar` SHA256 of
`5ef7a4c11ecd6c8a0a0f47e3bed0da5ce9fc0be05d5d5446d5a930c1d9440909`.
Its own `uv sync --frozen --no-dev --package openhands-agent-server` environment
runs the candidate. A separate Python environment containing only the pinned
WebSocket dependency runs the verifier. The local source/executable binding is a
development provenance assumption, not an authenticated deployment attestation.

The candidate reaches the real external scripted provider, persists the finish
result, replays durable history matching REST, and returns the exclusive suffix
on reconnect. Socket error frames have no `seq` and do not persist.

When another user message arrives after the completed run, the persisted suffix
has `seq 9` (execution status), `seq 10` (user message), and `seq 11`
(`last_user_message_id`). The live observer receives **9, 11, 10**. Fresh runs
reproduce the inversion. After SIGKILL and restart with the same artifact/storage,
the first 12 durable events and their sequence positions remain exactly equal in
both REST and session replay. Post-restart continuation delivers **13, 12** for
the newly committed suffix. The separate legacy smoke returns all 14 persisted
events and its allowed single non-durable `full_state` snapshot.

| Required scenario | Current proposed predicate result |
| --- | --- |
| `WS-REPLAY-001` | Fail: committed live durable arrival order differs from persisted sequence order. |
| `WS-RESTART-001` | Fail: persisted/replayed crash recovery succeeds, then the continued live durable order differs. |
| `LEGACY-EVENTS-001` | Pass: unwrapped legacy transport smoke with 14 persisted events. |

The overall result is **fail against the proposed stricter durable-order
contract**, not a declaration that an already accepted public API contract was
violated. The session implementation describes an ordered channel, while its
protocol explicitly does not impose order between open streaming items. The
manifest now distinguishes committed durable ordering from transient/streaming
ordering and labels activation as a separate decision. Do not sort live arrivals
or weaken the predicate just to relabel this candidate healthy.

`main-first-failure.json` preserves the first full-scenario diagnostic, before
receiving the entire failing live batch and before correcting the legacy
snapshot interpretation. `main-repeat.json` records complete inverted arrivals
and successful legacy smoke. `main-current-bundle.json` records the same behavior
against the final proposed bundle. The complete `ws_received`, REST baseline and
`restart_observed` fields make the interpretation independently reviewable.

## Cloud status

Credential-free deployment dry-run packages exactly six runtime files in a
tarball under the service's 1 MiB upload limit. Authenticated preflight stops with
`cloud_bearer_missing`. No upload, registration, dispatch, existing definition,
schedule, provisioning setting, GitHub status or public comment changed.

Cloud is a viable host for the trusted deterministic verifier. Separate isolated
candidate provisioning/artifact authentication, externally reachable fixture,
trusted restart control, scoped target/control capabilities, durable evidence
export and protected bundle/result activation remain explicit deployment
prerequisites. The supplied definition is disabled and uses an inert staging
cron. Full operational instructions are in the source README.
