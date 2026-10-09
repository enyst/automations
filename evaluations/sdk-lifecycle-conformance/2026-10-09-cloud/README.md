# Manual OpenHands Cloud lifecycle evaluation — 2026-10-09

This is the first retained **Cloud execution** of the merged external lifecycle
verifier. The automation was registered disabled, then dispatched manually twice.
The first run exposed a test-environment DNS restriction; the second completed
the scripted provider, replay, process-crash restart, and legacy transport checks.
Both exact `evidence.json` downloads are retained here. These are evaluation
results against the verifier's **proposed** persisted live-order predicate, not
an activated merge gate or a claim that an accepted public SDK contract was
violated.

## Subject and verifier

- Candidate: clean `OpenHands/software-agent-sdk@9f47d471ee6f91ba1d140d10d34f3b26d5ac2427`.
- Candidate `git archive --format=tar` SHA256: `5ef7a4c11ecd6c8a0a0f47e3bed0da5ce9fc0be05d5d5446d5a930c1d9440909`.
- Verifier contract/bundle digest: `f9126257dae7fd71f85a1ec0578cfac4dba952cb44c543c56f957d733c90c04c`.
- Effective configuration digest: `14320872783012436f7c2e3b3f2910d4977a9c7d3bd2c3f53fa6f7373717b40f`.
- Uploaded tarball SHA256: `d07ea6b987e9f45b5537b6a59e649341ac9358caca30fadc4500f38715307525`.
- Cloud automation: `481e4de6-48b1-46b3-a99b-985e7b35ebd3`, disabled with the inert staging cron.

The trusted local coordinator started the pinned candidate under a deny-default
macOS process sandbox, with an explicit credential-free environment and persistent
test storage. The candidate could not read the host's OpenHands Keychain item or
the coordinator's scoped keys. Temporary HTTPS routes exposed the key-protected
candidate and restart controller plus a scripted provider with a protected
witness endpoint to the separate Cloud verifier sandbox. The controller killed
the actual Agent Server process and started the same interpreter with the same
storage. The binding receipt
`local-seatbelt-87c79cd2e3744995b356368cf4af7745` identifies the local
operator record; it is **not** a cryptographic deployment attestation. This
manual setup does not establish a production admission boundary for untrusted
candidate code.

## Runs

| Cloud run | Exact evidence | SHA256 | Result |
| --- | --- | --- | --- |
| `61fec9d1-1a48-46ee-8a7c-84b35dd39e6f` | [First run](first-run.json) | `9e7884ecdbd371c852394ef87cba5ec488ef4a13cc9787942e09bffdb8534523` | Test setup fault: candidate sandbox DNS could not resolve the public fixture. |
| `fe8033ce-d97a-42f1-8100-c4cfbae2032c` | [Confirmed run](confirmed-run.json) | `7ee2f35768b254871fc547d01a23e6c65e22032ff6d75aec50e7db3adff5ef48` | Complete three-scenario evaluation; proposed live-order predicate fails. |

The first run's `scripted_run_did_not_complete` and
`restart_rest_history_changed` results are retained as a setup diagnostic, not
interpreted as SDK conformance findings. A narrow sandbox profile correction
allowed DNS and TLS certificate-path access while keeping credential access
denied. A sandboxed HTTPS probe then reached the fixture before the second run.

The confirmed run records the provider's nonce witness, a non-durable socket
error, exact persisted REST/replay recovery after restart, and 65 received
WebSocket frames:

| Required scenario | Confirmed result |
| --- | --- |
| `WS-REPLAY-001` | Fail: live durable cursor expected `10`, received `11`. |
| `WS-RESTART-001` | Fail: restored history matched, then live durable cursor expected `12`, received `13`. |
| `LEGACY-EVENTS-001` | Pass: 14 persisted events on the legacy transport. |

The candidate revision, artifact digest, bundle digest, configuration digest,
binding reference, and complete required roster were checked against the
downloaded bytes. A scan found none of the account bearer or three scoped test
keys in either evidence file. After export, both Cloud runner sandboxes and the
three temporary Cloud secrets were deleted; the HTTPS tunnels and local test
services were stopped. The inactive automation remains registered for deliberate
future use with a newly provisioned candidate and fresh run-scoped keys.
