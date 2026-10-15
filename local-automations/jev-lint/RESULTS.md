# Local experiment results · 2026-10-14

Model: `jev-1.13.0`. Three manual local automation runs completed, exit 0.
No coding agent or subagents ran. The automation remains paused. No source changes
or public review comments were made by the automation.

## Whole-file first attempt

Noul `P(violation)` on the original hooks service/router:

| Rule | Original | Corrected |
| --- | ---: | ---: |
| Specific suppress policy | 0.53 | 0.16 |
| Specific naming policy | 0.48 | 0.12 |
| General simplification question | 0.41 | 0.41 |
| General naming question | 0.36 | 0.35 |

None met the 0.95 experimental error threshold. These questions did not provide
useful strong correction signals. Lowering the threshold would hide that failure.

## Focused English policies

Each AST candidate gets the English rule and a focused violation question, with
both Noul and binary Choice answers. Original names versus resource-explicit names
are an explicit convention, not a universal correctness judgment.

| Candidate | First focused Choice P(correct) | Confirmation run | After fix, confirmation |
| --- | ---: | ---: | ---: |
| Public hook loading flags, request model | 0.99 | 0.99 | 0.00 |
| Same flags, service parameters | 0.98 | 0.99 | 0.00 |
| Missing Final on reviewed constant | 0.98 | 0.98 | 0.01 |
| Suppress alternative in `_same_file` | 0.79 | 0.75 | Candidate gone |

Suppress remained 0.84/0.81 in the names-only variant: still below the gate.
The logging handler stayed negative (confirmation Choice P(correct) 0.05).
No errors fired on either clean control or the final hooks files. The two
one-change variants showed naming diagnostics vanish after the naming fix while
suppress-only changes left naming diagnostics in place.

Noul was weaker than Choice for the positive Final example: 0.91 first focused,
0.94 confirmation; fixed 0.05/0.04. Naming Noul was 0.94–0.95 before, 0.05 after.
Choice was therefore selected for the error gate after seeing the comparison;
this is exploratory selection, not a preregistered evaluation. The confirmation
run emitted real structured naming and Final diagnostics with alternatives.

## Example diagnostic

```json
{
  "severity": "error",
  "code": "CONST_FINAL",
  "location": "pydantic_secrets.py:3",
  "message": "Annotate a module-level constant with typing.Final and its value type ...",
  "alternative": "Import Final from typing and annotate the constant as NAME: Final[its_value_type] = value. Keep the value unchanged; Final is a static contract, not runtime immutability.",
  "signal_probability": 0.98,
  "noul_probability": 0.94
}
```

The implementation includes a snippet SHA-256 too. Locations refer to fixture
lines; the Final fixture isolates the actual reviewed declaration, not the entire
file. `confirmation-results.json` preserves typed answers and diagnostics.

**Conclusion:** explicit, focused English lint policies worked for names and
Final in these examples. Suppress needs more work. These fixtures are too small
and too closely related to calibrate error thresholds or estimate false-positive
rates. Broader held-out compliant/violating code is required before using this
as a mandatory agent-loop gate. No automatic correction/recheck loop was launched.

## Offline verification

13 focused tests pass with system Python. The full repository suite passes: 308
tests with the existing SDK virtualenv (includes the websockets dependency absent
from system Python). Initial system-Python full run exposed test-module isolation,
now fixed, and three unrelated missing-websockets failures. No packages installed.


## MAP_CURRENT_BEHAVIOR — PR #18437 (2026-10-15)

Policy: the feature map describes current behavior, not fix history or older
checkouts. Once a fix is established, document its behavior directly. Keep the
map clean and concise for the next verification pass. Unresolved known defects
and current prerequisites remain allowed.

Actual Jev 1.13.0 calls, public source only. Base `8b9823a89ce5844d0808398c2f740d9748da66df`;
pre-edit head `617126104e422bcc19ab209111fd684b1f43e29f` (the PR head immediately
before Engel's two commits); post-edit head
`1a536a9b1b2e6735d431dabfed40a7e1e29d41fd`. Each diff contained 77 changed prose
paragraphs, evaluated in seven batches. No whole repository source was sent.
Case labels, revision IDs, reviewer comments and expected answers were not in
model state. Same rule and threshold for both runs; no threshold lowering.

| F10 paragraph | Before Choice P(correct) / Noul | After Choice P(correct) / Noul | Error before → after |
|---|---|---|---|
| Duplicate-name fix history / older checkout (line 113) | .97 / .93 | .07 / .13 | yes → no |
| Inherited-options fix history / older checkout (line 122) | .85 / .80 | .02 / .08 | no → no |

Thus the test catches the first human correction at the error gate but misses
the second. Both corrected paragraphs receive much lower violation signals.
Full pre-edit diff: three errors (F10:113, F13:85, F25:129), exit 2.
Full post-edit diff: three errors (F13:85, F22:115, F25:129), exit 2.
The unchanged F22 paragraph crossed the gate only on the second call: this also
shows classifier variability. Do not interpret the post-edit diff as clean.
F25 combines an unresolved defect with historical proof prose; review the flagged
paragraph and retain the unresolved reproduction, rather than deleting it whole.

These are development examples, not a holdout or calibrated reliability study.
Locations and alternatives come from the script. Responses and exact public
paragraphs are retained in `feature-map-results.json`. No product map was edited
by this test, no automation was dispatched or enabled, and no coding-agent hook
was installed.


## Gate changed to 0.80 (Engel, 2026-10-15)

MAP_CURRENT_BEHAVIOR now uses Choice P(correct) >= 0.80, still requiring
Noul > 0.5. Python-rule gates remain 0.95. Replayed the saved responses without
new API calls: 13 pre-edit errors, 10 post-edit errors. Both F10 old-checkout
paragraphs now emit errors before the edits; neither emits one after. Other
paragraphs still flag, so the full post-edit diff is not clean. The original
0.95 experiment above remains unchanged as historical test evidence. This score
is uncalibrated; a lint diagnostic requires inspection, not automatic deletion.
See feature-map-gate-080-results.json.
