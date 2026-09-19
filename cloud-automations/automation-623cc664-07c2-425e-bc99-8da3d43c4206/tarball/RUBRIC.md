# Notebook Field Notes selection · September 19, 2026

This automation selects its own investigations from public changes in
`OpenHands/OpenHands`, `OpenHands/software-agent-sdk`, and `OpenHands/automation`.
`OpenHands/agent-sdk` is normalized to `OpenHands/software-agent-sdk`. The script
must confirm source access through unauthenticated public GitHub reads. An
allowlisted name alone is not proof that fetched material is public.

## Deterministic discovery

Treat an issue and a pull request as distinct subjects. A subject has one stable
identifier, for example `field-note-openhands-software-agent-sdk-pr-321`.
Version fingerprints include the title, description, exact source commits,
changed-file excerpts and coverage flags. Updating a timestamp or the fully
delimited Jev Fast Audit scorecard alone does not count as a material change.
Human text following the scorecard remains part of the fingerprint.

The initial screen excludes draft PRs, already-published subjects and completed
fingerprints. Recently active merged PRs and closed issues remain eligible.
Failed or deferred work must never be added to completed fingerprints. A
candidate becomes completed only after a valid explicit selection result, or
after its publication artifact is durably committed.

Mechanical exclusions are deliberately narrow: an explicitly described lockfile
regeneration/reformatting with complete changed-file evidence, no dependency,
version, integrity, registry or source changes in its patch, and no design or
behavior signal in the title/description. Ordinary dependency updates go to Jev.
There is no minimum patch size, and design documents are eligible.

## Five independent Jev questions

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
| Substance | An explainable mechanism, failure or unresolved question worth investigating | A routine PR announcement or unsupported speculation |

The exact questions and positive/negative criteria live in `core.py`. A candidate
is selected when at least one of the first four probabilities is at least 0.65
and substance is at least 0.70. Runtime configuration may set stricter thresholds.
Multiple categories can pass; categories are never forced to compete.

Invalid model identity, missing answers, nonnumeric probabilities and values
outside `[0, 1]` raise errors. They are not negative classifications. A negative
result on incomplete context is deferred. A strong positive can proceed despite
bounded excerpts: the writing agent then inspects pinned source through its
restricted public-evidence tool. Selection is not a security audit or a claim
that every changed line was reviewed.

Context has an explicit byte budget, which is a transport bound and not a token
estimate. Description and patch truncation set incomplete coverage flags.
Question wording is never shortened to fit the budget. Titles, descriptions,
comments and code are untrusted evidence, not instructions for the automation.

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
small behavioral changes, incomplete evidence, structured answer validation,
source provenance, model attempts to choose metadata, reference exclusion and
inert rendering.
