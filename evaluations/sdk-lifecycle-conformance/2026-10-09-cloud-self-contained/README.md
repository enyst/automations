# Manual Cloud self-contained SDK evaluation — 2026-10-09

OpenHands Cloud run `485b6639-46a0-4d31-9c8d-39a24872d98c` reached transport
state **COMPLETED**. The verifier's separate verdict was **pass** for all three
required scenarios. This is an experimental positive control, not an active
merge gate or an independent admission result.

The [exact exported evidence](evidence.json) is retained without reformatting:
295,853 bytes, SHA256
`b2b81390c27c241bea29352cc1b7575c4bade89edff56fd352b79b3c2e76e315`.
The Cloud sandbox was deleted after the export was validated. This directory is
the dated record of that run. Earlier evaluations remain in their own directories.

## Subject and setup

- Pinned SDK revision: `9f47d471ee6f91ba1d140d10d34f3b26d5ac2427`.
- Verified `git archive --format=tar` SHA256: `5ef7a4c11ecd6c8a0a0f47e3bed0da5ce9fc0be05d5d5446d5a930c1d9440909`.
- Experimental verifier bundle SHA256: `b3591ececd90acd5503352d3f9984f070462ebf0a9be731a3c40a9e80b5c5bd5`.
- Effective configuration SHA256: `5cb4a107cac041a4b94d83be38313b77eafd6229e9a4446e50c771bd9de05532`.

The experimental archive placed the safe `setup_experimental.sh` source at the
root path `setup.sh`. Cloud ran that native setup hook before the verifier
entrypoint. Setup installed the verifier dependency and used the pinned SDK
lockfile to install both `openhands-agent-server` and `openhands-workspace`.
The evidence records `archive_verified: true` and confirms that the four
OpenHands imports resolved inside the pinned checkout.

The candidate Agent Server and scripted provider ran on loopback in the same
Cloud sandbox as the verifier. The verifier used synthetic keys and restarted
the candidate process while preserving its test storage.

## Observed results

| Scenario | Verdict | Evidence |
| --- | --- | --- |
| `WS-REPLAY-001` | Pass | 12 durable events; replay recovered 3 continuation events after cursor 4. The provider call was observed, and the socket error was not durable. |
| `WS-RESTART-001` | Pass | 12 durable events were restored. Storage and history survived the process restart; replay recovered 2 post-restart events. REST and replay comparisons matched. |
| `LEGACY-EVENTS-001` | Pass | 14 persisted events. This is a legacy transport smoke check. |

The evidence marks `execution_mode: co_located_positive_control`. The candidate
was trusted pinned source, but candidate and verifier shared one Cloud sandbox.
The child environment limited accidental credential delivery; it was not a
security boundary against malicious candidate code. The subject hash verifies
the Git source archive, not the installed executable or its dependencies.

A targeted scan of the exported JSON found two `api_key` fields, each replaced
by ten `*` characters. It found no OpenAI-style, GitHub-style, bearer, or private
key pattern. The evidence contains Cloud sandbox paths and stock Agent Server
prompt text. No account key or run-scoped key was added to this README.
