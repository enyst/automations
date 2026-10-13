# Historical dead-code canary · October 13, 2026

## Outcome

**Incomplete: infrastructure timeout.** The run ended at 15:54:53 UTC, before
any deletion patch, full verification, checkpoint report or draft PR. The remote
conversation was explicitly interrupted and read back as paused. Its retained
sandbox was paused and read back as PAUSED. Both automation definitions remain
inactive; no second dispatch or model change occurred.

The agent independently checked baseline/candidate SHA
`5a4071c764a9323c599e70258211b3f67c2aefad`. It ran Knip 6.41.0 in normal and
production modes, TypeScript unused-local analysis and reference/search checks.
Sanitized analysis files were retained privately; raw runtime metadata and logs
are not committed. This record is the coordinator's comparison, not an agent
claim that deletion safety passed.

## Comparison after execution

### PR #18417

The final PR deletes 14 production files. The canary's production-mode Knip
report listed **13 of those 14** as unused files; normal mode listed four.
Those include the edit-automation modal, timeout helper, Files-tab hooks,
barrels, file-priority/workspace-relative helpers and unused visual components.
`sanitize-query.ts` did not appear in either retained Knip report.
This is a file-candidate comparison, not coverage of every removed symbol or
proof that all those files are safe to delete. The two removed test helpers
also appeared in both Knip unused-file lists.

### PR #18406

The four overview panels were **not** reported as unused files. Knip did flag
seven exports in `conversation-overview-project-scope.ts`, including the
project-scope filters/counters. The report also mentions the panels under
unresolved imports; that is not a dead-code finding.

These panels have import references and render branches. Proving that no live
entry point selects those branches requires UI/state reachability analysis;
unused-import detection alone does not establish it. The agent reached an
inspection of overview-panel constants, but did not complete such proof.

## Limits exposed by the trial

- Knip reported 553 unresolved imports in normal mode and 469 in production
  mode. Its counts include scripts/entrypoints and cannot be treated as deletion
  instructions. Analysis setup needs qualification.
- The reference helper used `getReferencesAtPosition`, rather than the prompt's
  requested `findReferences`. It eventually resolved `useHasGitCommits` and
  returned its declaration plus three unit-test references.
- Earlier helper attempts had module/host errors. A `table-row-classes` query
  targeted line 1/column 1 and resolved no definition, which is inconclusive.
  Piping to `head` masked some failing Node exit codes.
- Most of the allotted runtime went into setup and individual reference/search
  steps. No candidate production diff or deletion experiment was produced.
- The checkpoint reminder was received, but no report file was written before
  timeout. Timeout ended the coordinator while the conversation still reported
  running; explicit interrupt/sandbox pause was needed.

Do not infer a model-quality comparison or a complete repository sweep from
this single interrupted trial. Improve deterministic analysis setup and
checkpoint/lifecycle handling before another authorized trial.
