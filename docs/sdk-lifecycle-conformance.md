# SDK external lifecycle conformance

**October 9, 2026:** The SDK lifecycle verifier is disabled in the October 9 Cloud export.

The canonical [runtime and settings](../cloud-automations/automation-481e4de6-48b1-46b3-a99b-985e7b35ebd3/)
come from the deployed Cloud version. These retained implementation notes record
earlier development and trials. See [current sync status](cloud-reconciliation.md)
before deploying.

A deterministic, external REST/WebSocket verifier for a running Agent Server.
The verifier imports no OpenHands package and does not patch the candidate's LLM,
conversation, persistence, transport, or event callbacks. A separate HTTP peer
returns a scripted OpenAI-compatible `finish` tool call. There are no paid
provider calls and no repair agent or GitHub publishing credential in this bundle.

## Scope and contract authority

`contracts.json` creates the required roster **before** execution. This first
bundle covers full durable replay, exclusive reconnect suffix, live continuation,
non-durable socket errors, process-crash recovery with preserved storage, and a
separate legacy event transport smoke. It does not certify the released
TypeScript client or OpenHands application, run ownership, cancellation,
confirmation, secret delivery, streaming chunk reconciliation, or storage-failure
guarantees.

The live continuation predicate currently requires persisted sequence order. This
is an explicit **proposed** contract, motivated by the session implementation's
description of an ordered channel and resumable durable cursor. Its activation
requires a decision about the intended public guarantee. The initial evaluation
records out-of-order live delivery on pinned main; it must not be relabeled a pass
by silently sorting arrival order. Transient `full_state` snapshots are allowed,
have no `seq`, and do not enter the durable history. The legacy endpoint's single
synthetic `full_state` event is likewise separate from replayed persisted events.

Implementation agents may propose amendments, but may not activate a weaker
bundle to accept their own changes. Before using this as admission, an independent
operator must approve an exact bundle digest, dependencies and configuration,
control the verifier execution, authenticate candidate artifact provenance, and
publish the result through the designated check identity. This repository alone
does not enforce those permissions or configure a GitHub merge gate.

If ordinary implementation agents receive the same personal Cloud account
bearer, that account's mutable automation definition is **not** an independent
activation boundary. A separately controlled accepted bundle/configuration pin
and result publisher must reject altered Cloud bundles, or the verifier must use
an identity whose management credentials implementation agents do not possess.
The account key injected into the Cloud runner makes this condition concrete;
placing the verifier in another repository or sandbox does not enforce it.

`bundle.sha256` hashes the named verifier/peer/contract/dependency/setup files
using filename, length and bytes. `configuration_sha256` hashes the effective
input. The subject contains an exact source revision and artifact SHA256 supplied
by a trusted coordinator; neither endpoint auth nor `/server_info` establishes
that the deployed artifact matches that subject. A receipt string is an evidence
reference, **not a verified attestation**. The independent consumer of evidence
must authenticate the receipt and deployment mapping.

The coordinator owns candidate teardown: retain failing storage and the evidence
until export/reproduction is complete, then destroy the target and revoke its
run-scoped capabilities. The verifier intentionally does not delete the
conversation or candidate after a failure, which would remove diagnostic state.
The local smoke owns and kills its subprocesses and removes temporary storage.

## Result and evidence contract

Exit codes are `0 = pass`, `1 = fail`, `2 = blocked`. Every required scenario has
one explicit status. Overall pass requires the complete roster to pass. Missing
identity, unreachable endpoints, dependencies, credentials or restart controls
cannot become a pass. A REST/WS observation that violates a predicate produces a
fail (including a 404 for a conversation already created and observed);
infrastructure inability produces blocked. A history snapshot that changes
while collecting live frames is blocked as an unstable baseline rather than
misattributed to the reconnect implementation. Unexpected harness errors also
produce blocked, with a closed error code.

`evidence.json` includes revision/artifact/bundle/config digests, the required
roster, received WS frames and REST/restart baselines. Only structured summaries
are printed; candidate event bodies stay in the evidence file. Known runtime
credentials are redacted before evidence is written. Request bodies and history
pages are bounded, WS frames are bounded to 4 MiB, the frame journal to 16 MiB,
and each verifier invocation has a 200-second budget within Cloud's
300-second job timeout. Remaining time is applied to blocking HTTP and WebSocket
operations; unfinished scenarios become `blocked / verifier_deadline_exceeded`
in the written evidence. Cloud setup, abrupt process termination and slow-drip
socket reads remain outside that guarantee. These are verifier budgets, not
general Agent Server performance promises.

Callback delivery errors are logged separately without changing the already
saved conformance verdict or the command's exit status. A callback receipt is
not evidence that the server passed the scenarios.

Cloud's transport run state has `COMPLETED`/`FAILED`, not a distinct blocked state:
both fail and blocked use the `FAILED` callback, while retained evidence preserves
the distinction. A dispatch `201`, a pending run, and a completion callback are
not substitutes for reading verifier evidence.

## Why OpenHands Cloud is viable, and what it does not provision

The inspected sources are:

- OpenHands/extensions `d008b81c44ae4d56d319e3b8f28ca35fc40aacbc`,
  `automations/README.md`, `automations/interface.json`, and
  `skills/openhands-automation/references/custom-automation.md`.
- OpenHands/automation `b04ef2417cdaf17ade33ebdc866cf3525134645b`,
  `openhands/automation/backends/cloud.py`, `dispatcher.py`, `schemas.py`, and
  `router.py`.
- enyst/automations base `381b1ba9770f2036e63605d59da9980fa430feb1`,
  `README.md` and `scripts/deploy_jev.py`.

Cloud can upload a custom tarball, validate and register its definition, manually
dispatch it, and expose its run history. Each automation run gets a fresh sandbox.
This is suitable for the **trusted verifier**. Cloud injects the user's
`OPENHANDS_API_KEY` and sandbox `SESSION_API_KEY` into the automation runtime.
The candidate must therefore run in a **different isolated environment** that
cannot read the verifier filesystem/environment, its Cloud credentials, GitHub
credentials, control endpoints or artifact store.

The inspected automation Cloud backend creates a default sandbox with
`POST /api/v1/sandboxes`, reads its `AGENT_SERVER` exposed URL and session key,
and does not select an arbitrary candidate image/artifact. Registering this
automation consequently does **not** provision a PR candidate. A trusted
coordinator must separately build/provision the pinned candidate, establish the
artifact-to-endpoint binding, preserve its storage across process restart, and
provide the target URL and run-scoped target key. Same-host subprocesses, a shared
Docker socket, or a separate conversation in the verifier's sandbox do not create
that isolation boundary.

The candidate's LLM `base_url` must reach an **externally reachable scripted peer**.
`127.0.0.1` refers to the candidate's host, not the Cloud verifier. `fixture.py`
can be hosted behind a dedicated TLS reverse proxy, or a trusted coordinator can
bridge the fixture service. Give the candidate access only to `/v1/*`; protect
`/control/requests` with a separate fixture control key and network policy.
The verifier requires a provider request-count increase **for the random nonce
in this run's submitted message** and the persisted finish result. Unrelated
traffic cannot qualify this run. The peer retains at most 256 nonce hashes and
counters, never raw requests or credentials. If concurrent traffic evicts the
run's witness, verification cannot pass.

The `restart_control_url` is a **coordinator integration contract**, not an
invented OpenHands endpoint. It accepts a POST with the pinned revision/artifact
and returns `{"restarted": true, "storage_preserved": true}` only after the old
process has died and the same artifact/storage is running. It must authenticate
its caller and bind that action to the independently provisioned candidate.
Absent this integration, restart remains blocked.

## Discovery and registration

The extensions public catalog discovers `automations/catalog/<id>/manifest.json`
and generates its catalog/bundle indexes. It supplies reusable UI templates; this
personal automation needs no public catalog entry.

The maintained runtime is the Cloud bundle under `cloud-automations/`.
`definitions/` holds explicit deployment templates. `scripts/` contains the
registration helper. Cloud Git Sync is currently blocked at credential storage,
so pushing this repository does not yet deploy it. Local sync uses the separate
`local-automations/` path.

## Local real-server development reproduction

Use a trusted, clean SDK checkout with its candidate dependencies installed:

```sh
# In that SDK checkout, prepare its own candidate environment.
uv sync --frozen --no-dev --package openhands-agent-server

# In this repository, prepare an independent verifier environment.
python3 -m venv /tmp/sdk-verifier
/tmp/sdk-verifier/bin/python -m pip install -r cloud-automations/automation-481e4de6-48b1-46b3-a99b-985e7b35ebd3/tarball/requirements.txt

/tmp/sdk-verifier/bin/python scripts/smoke_sdk_conformance.py \
  --candidate-python /absolute/sdk/.venv/bin/python \
  --candidate-repository /absolute/sdk \
  --evidence /absolute/output/evidence.json
```

The script computes the clean candidate's exact commit and `git archive` SHA256,
starts a real server subprocess with temporary persistent storage, starts the
external HTTP peer, executes the REST/WS verifier, SIGKILLs the server, and starts
the same executable/storage again. Candidate subprocess environment values are
an explicit allowlist; inherited account/GitHub/LLM credentials are not forwarded.
The verifier imports only its own files and its pinned WebSocket dependency.
The local script does not prove that an arbitrary `--candidate-python` installed
the same source archive: prepare it from that exact checkout as shown above.
The receipt is labeled `local-development-smoke-unattested`; source and executable
binding must be authenticated separately for production admission.

This local smoke is a **development positive control**, not a sandbox for
malicious candidates. A same-user subprocess can access its host. Run it only
against trusted code on a credential-free development host. Production Cloud
must use the separate candidate boundary described above.

Oracle negative controls and actual HTTP/WS redirect probes:

```sh
/tmp/sdk-verifier/bin/python -m unittest discover -s tests -p 'test_sdk_conformance*.py' -v
```

The test-only reference protocol server qualifies the full roster over real
loopback HTTP/WebSocket traffic and a real request to the scripted provider.
Its positive history passes every scenario; its deliberately inverted live
sequence fails the ordered-delivery predicate. It is an oracle qualification
fixture, not an alternate SDK implementation or evidence that a product candidate
passes. The actual Agent Server smoke remains separate and can fail the same
unchanged verifier.

## Explicit Cloud setup and manual run

The shipped configuration is intentionally incomplete and reports blocked.
Copy `examples/sdk-lifecycle-conformance.json` to a private runtime configuration file, supply the independent
candidate origin, exact revision/artifact digest, externally authenticated binding
receipt reference, reachable fixture origin, trusted restart-control URL and a
dedicated candidate working directory. Do not place credential values in it.

Provide these run capabilities through the Cloud account's secure secret
configuration (or narrowly scoped environment injection by a trusted operator):

- `CONFORMANCE_CANDIDATE_SESSION_KEY`: access to this candidate only.
- `CONFORMANCE_FIXTURE_CONTROL_KEY`: read-only request witness on the fixture.
- `CONFORMANCE_RESTART_CONTROL_KEY`: restart this candidate only, required
  only when `restart_control_url` is configured. Without it, replay can still
  execute and the independent restart scenario remains blocked.

The runner fetches **only those names** using its hosting sandbox's session key
at the exact Cloud origin. It never forwards a Cloud account bearer to candidate
or fixture. REST redirects and WebSocket redirects are rejected. Each target or
control key is sent only to its configured origin. All remote endpoints require
HTTPS; loopback HTTP is supported solely for local development.

The deployment helper reads `OPENHANDS_API_KEY` from the process environment.
An authorized operator should inject it without echoing it or putting its value
on the command line. No Keychain access or credential installation is performed.

```sh
# No network or credentials; review the exact inactive definition and bundle.
python3 scripts/deploy_sdk_conformance.py dry-run

# Requires a securely supplied account bearer. Read-only account/capability check.
python3 scripts/deploy_sdk_conformance.py preflight

# Explicit upload -> validate -> create -> inactive read-back. Does not dispatch.
python3 scripts/deploy_sdk_conformance.py create --config /private/runtime-config.json

# Paste the returned automation UUID, then dispatch and read back separately.
python3 scripts/deploy_sdk_conformance.py dispatch --automation-id AUTOMATION_UUID
python3 scripts/deploy_sdk_conformance.py status --automation-id AUTOMATION_UUID
```

These commands map to the actual `/api/automation/v1/uploads`, `/validate`,
`/v1`, `/{id}/dispatch` and `/{id}/runs` APIs. The helper checks the exact enyst
user/org identity before authenticated operations, refuses duplicate names,
registers `enabled: false`, and confirms inactivity before reporting registration.
It contains no activation/schedule update, candidate provisioning, GitHub write,
repair, or admission-result publishing operation. Registration remains subject to
the live service's capability/preflight validation; source inspection alone does
not establish which backend version is deployed.

`keep_alive: true` preserves the sandbox so the operator can retrieve
`evidence.json` before its runtime TTL expires. **That is not durable archival.**
Before scheduling unattended runs, a trusted coordinator must export the exact
evidence and its digest to durable storage and authenticate the result publisher.
The initial PR retains evaluation evidence; it does not implement Cloud archive
retention or silently claim that run status proves conformance.

After those prerequisites and contract activation, daily verification at 09:00
Europe/Stockholm is a reasonable maintenance cadence. Each run must receive an
independently pinned main/release artifact; this bundle does not follow moving
`main` or attest a deployment itself. Weekly exploration and repair should use the
same approved contracts and retained incident corpus. The shipped staging cron
and disabled state leave activation entirely explicit.

The first manual Cloud execution on **2026-10-09** is recorded in the
[evaluation evidence](../evaluations/sdk-lifecycle-conformance/2026-10-09-cloud/).
The automation remains disabled. Against pinned SDK main, the complete Cloud run
failed the **proposed** persisted live-order predicate in replay and restart;
the legacy transport smoke passed. This manual evaluation did not activate a
merge gate or establish cryptographic candidate deployment attestation.
