# OpenHands Automations

Configuration and source history for Engel's OpenHands automations.
Repository: `enyst/automations`, branch `main`.

## Current baseline · October 9, 2026

The deployed Cloud versions are the baseline. Each Cloud automation has one
runtime source directory: `cloud-automations/automation-<UUID>/tarball/`.
Edit that copy. Deployment tools and tests use the same files.
The four duplicate source trees under `sources/` have been removed.

The complete [Cloud export](cloud-automations/manifest.json) contains 13
automations: 6 enabled and 7 disabled. Every runtime file matches its downloaded
Cloud bundle. The export includes activation state and agent profile references.
No automation code, ID, schedule, or enabled state was changed in Cloud.

**Cloud Git Sync is not active yet.** The repository access check passed with
the existing Cloud `REMOTE_GH` secret, but saving that credential returned HTTP
503. Configuration writes without a secret succeeded. Sync remains disabled;
a GitHub edit does not yet deploy to Cloud. See the
[reconciliation record and remaining setup](docs/cloud-reconciliation.md).

## Where to edit

| Path | Purpose |
| --- | --- |
| `cloud-automations/automation-<UUID>/automation.yaml` | Observed Cloud settings. |
| `cloud-automations/automation-<UUID>/tarball/` | The sole maintained copy of each deployed Cloud bundle. |
| `local-automations/` | Local definitions and runtime files, managed by local native Git Sync. |
| `definitions/` | Templates for explicit staging or deployment helpers. They do not track live state and are not imported by Git Sync. |
| `docs/` | Implementation notes and operational records. |
| `tests/` | Offline regression tests, outside deployable bundles. |
| `examples/` | Unconfigured examples, outside deployable bundles. |
| `evaluations/` | Retained evidence from dated experiments. |

Do not add another runtime copy under `sources/`. Keep experiments in branches.
Before an explicit deployment, compare the current Cloud settings and bundle
with the proposed change. Before refreshing an export, commit or branch any
repository changes that must be kept: an export replaces files with Cloud's copy.
The older repository versions remain in
[Git history](https://github.com/enyst/automations/tree/50ed9d45c9b485b632a808a796357834c2c0c779).

## Cloud inventory

This table records the October 9 export. Check Cloud for later changes.

| Automation | State |
| --- | --- |
| [Mention Gazette](cloud-automations/automation-055b7b6a-5455-4243-b97f-843e16050ed3/) | Enabled |
| [SDK external lifecycle conformance](cloud-automations/automation-481e4de6-48b1-46b3-a99b-985e7b35ebd3/) | Disabled |
| [Temporary transpile native-auth verification 2026-09-22](cloud-automations/automation-e3ef72c3-764a-428a-9d31-a843fb7fa965/) | Disabled |
| [Notebook Field Notes](cloud-automations/automation-623cc664-07c2-425e-bc99-8da3d43c4206/) | Disabled |
| [Jev Fast Audit](cloud-automations/automation-5aee9a93-51e5-4843-ad13-301a31e1397e/) | Disabled |
| [Weekly re-vendor: smolpaws openhands-agent-server (SDK + server ports)](cloud-automations/automation-d4f8b4de-be10-4795-822b-72ca38dc323e/) | Enabled |
| [Weekly upstream drift: openhands-agent (OpenHands SDK -> TypeScript)](cloud-automations/automation-e6b527a2-5cd1-45d6-82c5-ac1cf89ebda6/) | Enabled |
| [QA Changes - Auto-Test PR description (software-agent-sdk)](cloud-automations/automation-1dd76c1d-5c79-4598-b19e-018ad893c6ee/) | Enabled |
| [Attention router - daily verification reports and weekly SDK PRs](cloud-automations/automation-fe2c8185-1b7f-41bf-a687-143e350408b6/) | Enabled |
| [Issue Duplicate Checker - auto-close sweep](cloud-automations/automation-7b7ca607-3052-476e-b9f9-63d98ed98971/) | Disabled |
| [Issue Duplicate Checker - detect](cloud-automations/automation-d0f69df6-4757-4403-8106-2013ae5e0db5/) | Enabled |
| [Roasted Code Review - OpenHands PRs (on behalf of @enyst)](cloud-automations/automation-de2d1215-bbb3-42a9-bec4-7feee4f19a86/) | Disabled |
| [Daily external PR security screen and review for OpenHands repos](cloud-automations/automation-e2ca316e-2896-4189-b1fc-fef6100d85f1/) | Disabled |

## Mention Gazette

[Mention Gazette](cloud-automations/automation-055b7b6a-5455-4243-b97f-843e16050ed3/)
runs in Cloud on Monday through Thursday at 09:00 Europe/Amsterdam. Its local
definition is disabled. It uses the existing `REMOTE_GH` secret and GitHub's
Contents API to update the Gazette page. It uses no local checkout and no LLM.

Run `dc437ef8-ef97-45ae-99d7-f32f5435aeed` completed successfully after the
completion callback was corrected. The
[published page](https://enyst.github.io/arch/mention-gazette.html) was verified.
The earlier publication is recorded in
[notebook commit bc94122](https://github.com/enyst/enyst.github.io/commit/bc94122ee7791c0aa5374dda60d9848da820e35f).

## Implementation notes

- [Jev Fast Audit](docs/jev-fast-audit.md): classifier inputs, evidence limits, and explicit deployment.
- [Notebook Field Notes](docs/notebook-field-notes.md): research selection and bounded publication.
- [SDK lifecycle conformance](docs/sdk-lifecycle-conformance.md): independent verifier and trial evidence.
- [Jev release security triage](plans/jev-release-security.md): a plan, not a deployed automation.

## Local Git Sync

The local configuration remains `main`, path `local-automations/`, with
`interval_seconds: 0` (manual sync). This cleanup did not trigger local sync.
Native sync pulls, imports, exports, then pushes. Changes to its settings or
runtime files can affect local automations on the next sync. Enabled does not
mean currently running.

## Validation

Use a temporary environment with the verifier's pinned WebSocket dependency:

```sh
python3 -m venv /tmp/automations-tests
/tmp/automations-tests/bin/python -m pip install -r cloud-automations/automation-481e4de6-48b1-46b3-a99b-985e7b35ebd3/tarball/requirements.txt
/tmp/automations-tests/bin/python -B -m unittest discover -s tests
```

The tests use synthetic fixtures; several open localhost sockets. They do not
validate paid classifier accuracy or dispatch live automations.

## Credentials

Credentials stay in the runtime, Cloud secret store, or macOS Keychain. Files may
name secret references but must not contain secret values. No credential was
copied from the Mac into Cloud during this setup. Do not put credentials in Git
URLs or use plaintext storage to work around the failed Git Sync save.
