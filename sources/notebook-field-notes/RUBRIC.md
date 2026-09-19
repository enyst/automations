# Notebook Field Notes selection · September 19, 2026

This automation selects its own investigations from public changes in
`OpenHands/OpenHands`, `OpenHands/software-agent-sdk`, and `OpenHands/automation`.
`OpenHands/agent-sdk` is normalized to `OpenHands/software-agent-sdk`. The script
must confirm source access through unauthenticated public GitHub reads. An
allowlisted name alone is not proof that fetched material is public.

## Deterministic discovery

Treat an issue and a pull request as distinct subjects. A subject has one stable
identifier, for example `field-note-openhands-software-agent-sdk-pr-321`.
Policy version **2** uses descriptions for triage. Version fingerprints include
that policy version, the title, description, linked issue descriptions, source
identity and coverage flags. Updating a timestamp or the fully delimited Jev Fast
Audit scorecard alone does not count as a material change. Human text following
the scorecard remains part of the fingerprint. The candidate's original body is
retained for freshness checks; only the classifier copy omits the scorecard.

The collector supplies `linked_issues` rows with a public issue URL, title, body
and optional `updated_at`, plus `description_complete` and
`description_truncated`. Up to four linked issues are retained. Missing retrieval
or additional omitted issues make coverage incomplete. An empty description
successfully fetched from GitHub is distinct from a failed fetch.

The initial screen excludes draft PRs, already-published subjects and completed
fingerprints. Recently active merged PRs and closed issues remain eligible.
Failed or deferred work must never be added to completed fingerprints. A
candidate becomes completed only after a valid explicit selection result, or
after its publication artifact is durably committed.

There is no minimum patch size, and design documents are eligible. The current
collector does not retrieve changed-file patches for triage. The older optional
mechanical screen still requires complete file evidence and never sends it to
Jev; it does not apply to the new description-only candidates.

## Six independent Jev questions

The pinned classifier is `jev-1.13.0`, using TypeSafe's
`https://api.typesafe.ai/v1/systemone`. The existing Cloud secret name is
`TYPESAFE_API_KEY`; only the runtime reads its value. Each answer is a Noul:
an estimated probability, not measured confidence or a statement of fact.

| Question | Relevant evidence | What does not establish relevance |
| --- | --- | --- |
| Design | Interfaces, state, lifecycle, ownership, architecture or tradeoffs | Large diff size alone |
| Agent behavior/performance | Tool loops, reasoning, observations, recovery, evaluation, latency or cost | An incidental mention of AI |
| Memory | Retention, retrieval, compaction, replay or context trust boundaries | Ordinary RAM allocation alone |
| Cross-repository | A concrete contract spanning at least two allowed repositories | Merely naming another repository |
| Substance | An explainable mechanism, failure or unresolved question worth investigating | Routine administrative work, even if fully described |
| Design context | Intent, motivation and intended behavior are clear enough for this change's scope | Vague or empty text that does not explain what changes or why |

The exact short questions and positive/negative criteria live in `core.py`.
Design context measures description sufficiency, separately from whether the
topic is interesting. A clear one-line routine fix can have sufficient context;
the rubric does not demand architecture documents for every change.

Decisions are evaluated in this order:

1. Incomplete retrieval or any truncated description: **defer**, regardless of
   probabilities. The automation's missing input must not be blamed on an author.
2. Design context at or below 0.30: **needs information**, even when the interest
   probabilities are low. Vague text must not disappear into the uninteresting
   bucket merely because its purpose cannot be understood.
3. Design context below 0.70: **defer** the ambiguous case.
4. Otherwise, at least one of the four topic probabilities at or above 0.65 and
   substance at or above 0.70: **write**; a clearly described, uninteresting topic
   is **skipped**.

These thresholds are estimated probability gates, not rankings or calibrated
quality scores. Multiple topic categories can pass. The runtime separately
controls whether a needs-information decision may produce one bounded comment
on an open PR; the core itself performs no external writes.

Invalid model identity, missing answers, nonnumeric probabilities and values
outside `[0, 1]` raise errors. They are not negative classifications. Live
responses must include all six answers. If selected, the writing agent performs
the deeper investigation using pinned source through its restricted public
evidence tool. Selection itself is not a code review or security audit.

Jev receives only the subject title/description, linked issue titles/descriptions,
the three repository names and coverage flags. A strict whitelist excludes
fetched source, diffs, changed-file paths, comments and commit metadata. Enormous
patches supplied by another caller are discarded before classifier validation.

The state is bounded to **24,000 UTF-8 bytes** (or a smaller requested bound),
which is a transport limit, not a token estimate. When needed, a shared byte cap
distributes the available space across descriptions and keeps valid Unicode.
Any truncation sets an explicit coverage flag and prevents author-facing requests
for context. Question wording is never shortened. Descriptions remain untrusted
evidence, not instructions. TypeSafe's larger documented model limits do not
justify sending unrelated source to this focused classifier.

## Public artifact boundary

The writing agent returns exactly `title`, `summary`, `body_markdown` and `tags`.
Trusted code supplies IDs, public visibility, dates, author, actual writer model,
source metadata, examined commits and classifier results. The model cannot
choose a destination, modify the schedule, change permissions, or set credentials.

An artifact has `schema_version: 1`, `id`, `subject_id`, `title`, `summary`,
`body_markdown`, `generated_at`, `author: "OpenHands Automation"`, `writer_model`,
`tags`, `visibility: "public"`, `source`, `examined_commits`, `classifier` and a
version `fingerprint`. The `source` carries repository, PR/issue kind and number,
canonical URL, timestamp and immutable `head_sha` (the default branch's inspected
commit for an issue), optional `base_sha`, and optional `linked_subjects`.
New artifacts preserve all six classifier probabilities, including
`design_context`. Legacy artifacts with the original five probabilities remain
valid and are not rewritten; they are never treated as new six-question live
responses. The artifact's schema version remains 1.

Only the following subject tags are accepted: `architecture`, `agent-behavior`,
`agent-performance`, `memory` and `cross-repo`. Trusted code adds `field-notes`.
Publication writes `field-notes/<id>.json` and `field-notes/<id>.html`. The manifest
is `{schema_version: 1, notes: [{id, path, sha256}]}`, with JSON paths fixed as
`field-notes/<id>.json` and SHA-256 over the exact JSON bytes. Publication creates
a note once; later runs must not replace it or an administrator's edits.

Markdown supports headings, paragraphs, simple inline links/code, bullets and
fenced snippets. It excludes active HTML, images, reference-style links and
unapproved link destinations. Source links must point to the three allowed
repositories at commits actually inspected by the writer; at least one pinned
source link is required. PR/issue links must identify the originating or explicitly
linked subjects. Working files named `*-ref.*` cannot become evidence links.
Credential-shaped strings and email addresses fail validation. These checks are
defense in depth: the research input boundary, not text scanning, is what keeps
private material out of public notes.

The renderer escapes all text and never executes imported HTML or code snippets.
Notes are visibly attributed to an autonomous OpenHands investigation. They
describe observations, hypotheses and remaining uncertainties separately; they
do not imply that Engel personally endorses the conclusions.

## Local policy checks

```sh
python3 -B -m unittest discover -s sources/notebook-field-notes -p test_core.py
```

These tests run without network access, credentials, model calls or publication.
They cover alias deduplication, material-change identity, public destinations,
small behavioral changes, description-only classifier inputs, Unicode truncation,
missing-context decisions, legacy five-score artifacts, structured answer validation,
source provenance, model attempts to choose metadata, reference exclusion and
inert rendering.
