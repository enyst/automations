# Notebook Field Notes selection · September 20, 2026

This describes maintained source policy **3**. It does not establish that policy
3 has been deployed; dated deployment evidence remains in README.md.

This automation selects its own investigations from public changes in
`OpenHands/OpenHands`, `OpenHands/software-agent-sdk`, and `OpenHands/automation`.
`OpenHands/agent-sdk` is normalized to `OpenHands/software-agent-sdk`. The script
must confirm source access through unauthenticated public GitHub reads. An
allowlisted name alone is not proof that fetched material is public.

## Deterministic discovery

Treat an issue and a pull request as distinct subjects. A subject has one stable
identifier, for example `field-note-openhands-software-agent-sdk-pr-321`.
Policy version **3** uses descriptions for triage. Version fingerprints include
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

The initial screen excludes draft PRs and already-published subjects. Recently
active merged PRs and closed issues remain eligible. Failed or deferred
investigations have a 24-hour retry cooldown. Selected topics waiting only for a
writing slot can use their cached judgment sooner when a slot opens, after fresh
source and policy checks. Policy 3 invalidates older decision
caches; durable publication remains the permanent duplicate guard. Failed or
deferred work must not be recorded as successfully published.

There is no minimum patch size, and design documents are eligible. The current
collector does not retrieve changed-file patches for triage. The older optional
mechanical screen still requires complete file evidence and never sends it to
Jev; it does not apply to the new description-only candidates.

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
| Substance | An explainable mechanism, failure or unresolved question worth investigating | Routine administrative work, even if fully described |
| Design context | Problem, affected components and intended behavior are clear enough to begin implementation at this scope | Topic interest alone, or text that names a change without explaining what it should do or why |

The exact short questions and positive/negative criteria live in `core.py`.
Their exact keys are `design`, `agent_behavior`, `memory`, `substance` and
`design_context`. Cross-repository interaction is no longer a separate classifier
question or selection criterion. It can still be described in a note and tagged
`cross-repo`.

Design context measures implementation readiness, separately from whether the
topic is interesting. A clear one-line routine fix can have sufficient context.
An author need not solve the design question or know a bug's cause first; a clear
problem and intended behavior can be enough to begin investigating implementation.

Incomplete retrieval or any truncated description produces **defer**, regardless
of probabilities, and never requests author clarification. With complete,
untruncated input, the policy makes two independent decisions:

1. **Note selection:** at least one of `design`, `agent_behavior` or `memory` at
   or above 0.65, and `substance` at or above 0.70, selects **write**. Otherwise,
   select **skip**. `design_context` does not affect this decision.
2. **Clarification:** `design_context` at or below 0.30 sets a separate
   `needs_info` Boolean. It is a comment signal, not a note-selection bucket.
   Higher context values do not trigger automatic clarification or deferral.

These thresholds are estimated probability gates, not rankings or calibrated
quality scores. Multiple topic categories can pass. A subject can qualify for
both writing and a clarification request. The runtime permits at most one
automatic comment attempt per open, nondraft PR, and two attempts per UTC day.
The fixed comment asks the author to update the PR description or linked issue;
discussion comments are not classifier input. Writing budgets do not stop these
checks, including after the invocation's one writing investigation has run.
The core itself performs no external writes.

Invalid model identity, missing answers, nonnumeric probabilities and values
outside `[0, 1]` raise errors. They are not negative classifications. Live
responses must contain exactly the five policy 3 answers, with no missing or
extra keys. If selected, the writing agent performs
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
New artifacts preserve the five policy 3 classifier probabilities, including
`design_context`. Artifact validators accept exactly these historical key sets:

- Policy 1: `design`, `agent_behavior`, `memory`, `cross_repo`, `substance`.
- Policy 2: the policy 1 keys plus `design_context`.
- Policy 3: `design`, `agent_behavior`, `memory`, `substance`, `design_context`.

Other missing or extra score sets are invalid. Historical artifacts and tags
remain intact; accepting their stored provenance does not make the older answer
schemas valid for live classification. The artifact's schema version remains 1.

Only the following descriptive subject tags are accepted: `architecture`, `agent-behavior`,
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
independent note/comment decisions, all three artifact score schemas, structured answer validation,
source provenance, model attempts to choose metadata, reference exclusion and
inert rendering.
