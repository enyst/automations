# Jev evaluation: open SDK issues

**My assessment: Jev is useful for finding relevant topics, but the current policy is not reliable enough to be the final editorial selector or the sole reason for an author-facing “not enough information” comment.** It recognizes strong agent/design subjects. The main problems are the sufficiency gate, repository-boundary tagging, and the distinction between evidence omitted by our collector and evidence missing from an issue.

This is a separate evaluation. It made no changes to production automation, prompts, thresholds, Cloud configuration, website content, or GitHub discussions. A concurrent transport-only commit advanced the repository while this ran; the classifier policy remained unchanged and this experiment stays pinned to the original rubric.

## What was tested

- Repository: OpenHands/software-agent-sdk, public issues only.
- Snapshot completed **2026-09-19 21:49:22 UTC** (23:49 in Stockholm); reviews finished on 20 September. All **270 open issues** from six REST pages, excluding pull requests.
- Observed source main: bd5fff06c2fe79d6e0e5d192bbf22339482f2805.
- Rubric/policy frozen from private automations commit 0ad0d32094c2d6558e830343d3102e4da8ef15b9, policy version 2, model **jev-1.13.0**.
- Input: title and issue description, watched-repository names and coverage metadata. No comments, linked PR/issue bodies, diffs, code retrieval, or image interpretation.
- The production UTF-8 input bound was retained. Issues **#4643 and #5074** were truncated and conservatively deferred by the coverage guard.
- All 270 primary calls succeeded, with no retries. Every exact request, structured response and normalized decision is saved. A further 20 responses test repeatability on ten selected boundary cases.
- This deliberately bypasses production's recent-update discovery window and daily budget. The 270 issues are an inventory snapshot, not a claim that production will process this backlog.

The provider returned six numeric scores, not prose explanations. Explanations below are my analysis of the issue descriptions and those scores; they are not attributed to Jev. Scores have not been calibrated as probabilities of correctness.

## Primary results

| Current route | Count | Meaning in this experiment |
| --- | ---: | --- |
| write | 183 | Eligible for a researched note; no note was generated |
| defer | 54 | Context uncertainty or input coverage guard |
| skip | 28 | Insufficient topic relevance/substance under the thresholds |
| needs_info | 5 | Context score at or below 0.30; no comment was posted |

The 67.8% selection rate is broad, but this backlog contains many substantive architecture and agent issues. Selection rate alone is not evidence of poor precision. The independent review comparison below is more informative.

## Review method

Three agents independently reviewed disjoint randomized groups without seeing Jev output, other reviewers, GitHub labels, or external context. One reviewer subsequently took the final 20 cases of a second group, without overlap. Every supplied issue text received a blind semantic review; reproducer claims were not independently executed or verified.

I also reviewed a random 18-case sample before opening the scores. Afterward I inspected all score/route rows and re-read the cases that best exposed disagreements and policy risks. My original blind labels remain frozen; [targeted assessments](reviews/root-targeted-assessment.json) distinguish later editorial judgments.

These are independent **agent judgments**, not human labels or ground truth. Editorial value is subjective. Agreement statistics must not be described as model accuracy.

## Independent comparison

All 270 inputs have a validated, non-overlapping blind review. Reviewers would investigate 219, skip 40, leave 9 uncertain, and mark 2 as insufficiently described in the supplied text.

| Blind recommendation | Jev: write | Jev: defer | Jev: skip | Jev: needs_info |
| --- | ---: | ---: | ---: | ---: |
| Investigate | 169 | 42 | 8 | 0 |
| Skip | 13 | 7 | 20 | 0 |
| Uncertain | 1 | 5 | 0 | 3 |
| Insufficient description | 0 | 0 | 0 | 2 |

Thus **169 of 183 selected topics** also received an investigate recommendation. That supports Jev's usefulness as a topic finder. Conversely, **50 of 219 reviewer-recommended topics did not pass**: 42 deferred and 8 skipped. One of those 42 deferrals was the appropriate truncation guard (#4643), not a context judgment. The other truncated issue (#5074) was marked uncertain by its reviewer and also deferred.

Using the stated mapping investigate→write, uncertain→defer, skip→skip, insufficient→needs_info gives **196/270 (72.6%) route agreement**. This is agreement with agents, not accuracy. The reviewers themselves disagreed with my blind sample on two of 18 actions: the fork-specific test proposal #4262 and the heredoc report #4957. We agreed on **16/18**. That small sample does not establish a population reliability rate.

Category agreement is also affected by class imbalance: most SDK issues describe understandable, relevant software problems. See [summary.json](summary.json) for yes/no/unclear counts and both disagreement directions rather than relying on a single percentage. [comparison.csv](comparison.csv) contains all cases; [root-overlap.csv](root-overlap.csv) preserves the double-reviewed sample.

## What works

Jev often identifies exactly the mechanisms we care about: state and lifecycle boundaries, agent/tool behavior, context retention, and contract changes. Separating relevance, substance and completeness is useful. Clear routine tasks can be skipped even when their description is complete—for example the provider revenue-share/catalog issue [#4574](https://github.com/OpenHands/software-agent-sdk/issues/4574).

The description-only approach also avoids sending large diffs. Two long issue descriptions still hit the input bound, so explicit coverage tracking remains necessary.

## What I would change before relying on it

### 1. An open design question can already have enough context

[#4758](https://github.com/OpenHands/software-agent-sdk/issues/4758) explicitly asks what to do with TypeScript local execution and presents retain, mark experimental, and remove alternatives. Jev recognizes design relevance at **0.95** and substance at **0.81**, yet context **0.52** makes the policy defer it.

[#4544](https://github.com/OpenHands/software-agent-sdk/issues/4544) describes triggered skills disappearing after condensation while durable activation markers prevent reinjection. It includes concrete impact, reproduction claims and design alternatives. Scores are **0.95 design / 0.96 agent behavior / 0.98 memory / 0.92 substance**, but context **0.66** still blocks it.

[#4990](https://github.com/OpenHands/software-agent-sdk/issues/4990) explains why ACP work continues after the API reports PAUSED, gives observations and two possible contracts, and links the Canvas expectation. Context **0.67** defers it.

I would make sufficiency explicitly accept a well-posed problem or design question without requiring a chosen solution. The wording asks about “PR and linked issue descriptions” and “the change” even for a standalone issue. Separate useful-but-unresolved from genuinely uninterpretable.

### 2. Package boundaries are not repository boundaries

[#5043](https://github.com/OpenHands/software-agent-sdk/issues/5043) describes Python/TypeScript behavioral parity inside the SDK monorepo; Jev scores cross-repo **0.84**. [#4912](https://github.com/OpenHands/software-agent-sdk/issues/4912) is about path-gated CI for packages inside that same monorepo; it gets **0.85**.

Conversely, [#4696](https://github.com/OpenHands/software-agent-sdk/issues/4696) explicitly preserves SDK wire fields required by the OpenHands Canvas guard, but its cross-repo **0.60** misses the tag threshold.

Supply the current package-to-repository map and require a concrete named pair plus the stated contract before applying this tag. Both false-positive examples remain good design topics; their tag is the problem.

### 3. Missing input is different from a deficient issue

The five needs_info cases are [#4247](https://github.com/OpenHands/software-agent-sdk/issues/4247), [#4248](https://github.com/OpenHands/software-agent-sdk/issues/4248), [#4250](https://github.com/OpenHands/software-agent-sdk/issues/4250), [#4357](https://github.com/OpenHands/software-agent-sdk/issues/4357) and [#4433](https://github.com/OpenHands/software-agent-sdk/issues/4433).

Several contain screenshots or explicit links to a test matrix/PR which this experiment did not retrieve. Some are sparse, but **we cannot infer that the author failed to explain a change merely because our bounded request lacks the linked explanation**.

Before an automatic comment, distinguish:
- fetched text is complete but still unintelligible;
- additional referenced material could answer the question;
- topic is understandable but its editorial value is uncertain;
- context was truncated or retrieval failed.

The existing production comment path is limited to open PRs, not standalone issues. This experiment did not exercise that path or prove it reliable. It demonstrates why the same context score should not by itself authorize a public comment.

### 4. Relevant does not automatically mean a good next note

Jev selects the duplicated .git suffix bug [#4520](https://github.com/OpenHands/software-agent-sdk/issues/4520) and missing-newline insertion bug [#4583](https://github.com/OpenHands/software-agent-sdk/issues/4583). They are useful reports, but I would prioritize deeper design questions over these routine repairs.

There is legitimate disagreement at the boundary. I find the UTF-8 sampling issue [#5038](https://github.com/OpenHands/software-agent-sdk/issues/5038) potentially valuable as an explanation of validation boundaries; its blind assigned reviewer would skip it. That is an editorial disagreement, not established classifier failure.

We should group related issues before spending note slots: #5099/#5134/#5168 describe the same telemetry failure; #4934/#5173 concern the same MiniMax setting; #4901/#4910 concern ACP orphan processes. An epic and its substeps may deserve one coherent note rather than separate repetitive pieces. This experiment tested classification, not publication quality or prioritization.

### 5. Hard cutoffs amplify small score changes

I selected ten cases closest to context **0.70** before making repeat calls, excluding truncated inputs. Each exact request was repeated twice. **Five of those ten changed route** across the three answers: #481, #4262, #3442, #4555 and #4744.

This is a deliberately boundary-selected sample, **not an estimate that half of all classifications are unstable**. The shifts were small, but 0.69 versus 0.70 changed behavior. I would use a gray area for editorial review and avoid treating one borderline score as an irreversible decision. [Exact repeat results](stability/results.json) and [selection plan](stability/plan.json) are saved.

## Suggested next experiment

Keep the architecture simple: revise the wording and repository map; explicitly label “needs referenced context”; leave diffs out of Jev's input; group duplicate topics; compare a revised rubric against these frozen examples plus a fresh held-out set. Tune criteria before moving thresholds.

For research selection, useful unresolved questions should reach the researcher. For an author-facing request, require identifiable missing information after checking the available linked descriptions. Do not automatically turn uncertainty about a note into a demand that an issue author write more.

I have not applied those changes in this evaluation.

## Saved evidence and reproduction

- [Snapshot](snapshot.json), [frozen rubric](rubric.json), [frozen policy](core.snapshot.py).
- [Exact requests](requests/), [raw primary responses](responses/), [normalized results](results.json).
- [Blind inputs](inputs/), [independent reviews](reviews/), [repeatability data](stability/).
- The separate runner is scripts/evaluate_field_notes.py; it never calls the automation runner, publisher or comment client.
- Three offline regression tests cover response/request preservation and no duplicate calls on successful resume, saved HTTP failures with unique resumed attempt numbers, and credential-echo redaction. No credentials are saved in the evaluation.

Offline validation and report regeneration (from the automations repository):

    python3 -B scripts/summarize_field_notes_evaluation.py evaluations/notebook-field-notes/2026-09-19-sdk-open-issues --check
    python3 -B scripts/summarize_field_notes_evaluation.py evaluations/notebook-field-notes/2026-09-19-sdk-open-issues
    python3 -B -m unittest discover -s tests -p test_field_notes_evaluation.py

Validation reconstructs all requests from the frozen source, checks hashes and exact quoted evidence, compares saved answers and decisions, and verifies review coverage before generating reports. It makes no network calls. The API runner uses the existing TypeSafe Keychain credential directly in memory; it does not save the credential.
