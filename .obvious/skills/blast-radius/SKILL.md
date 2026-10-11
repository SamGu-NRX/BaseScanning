---
name: blast-radius
description: "Assess what a change could break beyond its diff. Trace indirect consumers and test the one or two facts its safety depends on. Read-only assessment for an explicitly requested blast-radius review."
disable-model-invocation: true
---

# Blast radius

Find what a change could break somewhere else before it ships. Listing direct callers is only the start. Follow dependencies a symbol search misses and establish the one or two facts that make the change safe or unsafe.

## Read-only assessment boundary

Do not fix the change, edit source or tests, change configuration or dependencies, check out refs, create a worktree, commit, push or deploy. Use source/history reads and existing safe tests. Inspect commands and imports before running them; tests and module initialization can write files or call services. No production data, external writes or migrations. If a proof needs writes, propose the exact test or ask for narrowly scoped permission to create a temporary probe. A request to assess is not that permission. Never remove someone else's artifacts.

Use capabilities exposed by the current host. No Cursor-specific settings, missing companion skills or model roster is required. Do not install tooling or change provider settings to complete an assessment.

## How strong is the evidence?

For each safety fact, reach the strongest level that is useful and safe, and state where you stopped.

1. Hypothesis only. Unsupported on its own.
2. Source-backed. Cite the actual `file:line`, pinned library source or contract.
3. Failure path traced. Walk the bad case step by step and show why it can or cannot occur.
4. Executed. An existing safe test or an explicitly authorized probe calls the real code and fails if the fact is false. Report the exact command, version, inputs and result.
5. Reproduced in the application. Exercise the relevant path using an available non-production runtime capability and report the observed outcome.

A test proves only the cases and environment exercised, not universal safety. Mark a fact below level 4 `Unproven by execution`; source reasoning can still be useful. Missing tools or test permissions are evidence gaps, never a pass. One small decisive test is better than a long plausible report.

## Assessment steps

1. Resolve the target and base/head revisions, including any requested uncommitted files. Read both sides of the diff and the changed, added and deleted symbols. State the actual behavioral change, including effects the diff does not spell out. Ask only when missing scope changes the target.
2. Anchor the mechanism and history. Trace the entry point, callers, callees, data flow and ownership in the relevant source. Use targeted `git blame -L`, `git log -n 20 -- path`, selected `git show` patches and `gh pr view` for linked PRs and issues. Follow renames when relevant. Read design comments or decision records tied to this change. History supplies intent; current code supplies behavior. Cite each separately, label inference and preserve contradictions. Do not query every available service or scan the repository by default.
3. Find the decisive safety fact. For example, does an invalidation call drop only already-dead cache entries, or can it affect live requests? Identify the exact precondition, who establishes it and whether every relevant caller preserves it. Do not assume every change has one magic invariant; retain a second fact when a separate failure path requires it.
4. Look beyond grep. Check the dependency's pinned version and local patches. Read relevant library source through the host's repository-cache workflow; use Context7 for current API documentation, without confusing it with proof about the pinned implementation. Trace microtasks, cancellation, unmount and teardown. Follow indirect contracts such as API JSON, database columns, wire formats, configuration, feature flags, another language reading the same bytes and consumers several hops downstream. Bound each expansion by the safety fact and name excluded consumers.
5. Test the fact against the cheapest decisive failure case. Prefer existing isolated tests that exercise the real implementation and can run within the read-only boundary. Include a counterexample or regression case where feasible. If no safe test exists, give the proposed test and mark execution unproven. Do not run an unsafe command merely to reach evidence level 4.
6. Separate confirmed concerns from hypotheses and cleared cases. Each concern states the triggering condition, failure mechanism, affected consumer, source location and user or operational cost. Estimate likelihood qualitatively with supporting evidence; do not invent probabilities. An empty search establishes only that the stated search found nothing.
7. For a wide change, a disputed invariant or a consequential unproven assumption, consider one fresh read-only review if the host supports it and current delegation rules permit it. Give the reviewer the exact target, safety question and evidence paths. Check its claims against source and tests. Do not start a multi-model competition or import a missing orchestration bundle. If review is unavailable, state that gap and continue the bounded assessment.

## Return

- What changed and the exact reviewed scope.
- The one or two safety facts, their evidence levels and decisive source or executed result. Clearly mark unproven execution.
- Confirmed concerns, with failure path, `file:line`, impact, evidence for likelihood and cheapest check. Keep unresolved hypotheses separate.
- Cleared cases, with evidence that rules them out within the tested scope.
- The next test or reproduction needed before merge, including any missing permission or runtime capability.

Apply the existing `unslop` writing guide. Return findings directly, not a report file. Strip private data before public sharing; this assessment does not authorize posting or merging.
