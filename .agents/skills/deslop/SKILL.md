---
name: deslop
description: Code quality review that reports AI slop, small local design issues, and larger structural refactors with a follow-up prompt. Explicit cleanup requests permit local, behavior-preserving fixes. On TypeScript and JavaScript diffs, also checks low-evidence typing, including casts, widening, and mocks that hide what the author already knew. Use when reviewing a diff, cleaning touched code, removing AI slop, or cleaning a diff before a commit.
---

# Cleanup-first code quality review

Check the diff against `main`. If the user names a different scope, use that
scope instead. A review request reports findings without editing files. An explicit
cleanup request, or a commit workflow that asks for cleanup, permits local,
behavior-preserving edits. In that edit mode, fix the little things first,
then do a brief code-quality pass on the touched area.

When a commit workflow invokes this skill, the intended staged snapshot is the
scope. Stay inside that boundary so the commit can restage without picking up
unrelated work.

## Coverage

- Inventory every changed path in the chosen diff. Inspect every changed hunk; do not rely on a representative sample.
- Account for code, tests, documentation, configuration, migrations, generated files, and lockfiles. Use the right review depth for each file type, but do not silently skip any path.
- Recheck the inventory after cleanup. New or materially changed hunks require another pass.
- Tools such as linters, typecheckers, tests, and searches support the review; they do not replace reading the diff.
- On TypeScript or JavaScript hunks, if the repo already runs anti-slop Oxlint rules, use their output on the touched files as extra evidence; otherwise check the same problems by reading the diff.

## Direct fixes in edit mode

- Extra comments that are unnecessary or inconsistent with local style
- Stale, misleading, or obvious comments; preserve comments that explain intent, constraints, ownership, or surprising behavior
- Defensive checks or `try`/`catch` blocks that are abnormal for trusted code paths
- `any`, unsafe casts, or loose types used only to bypass real contracts
- Inline imports in Python that should live with the rest of the imports
- Deeply nested code that should be flattened with early returns or a small helper
- Vague, misleading, or needlessly abbreviated names for variables, functions, types, files, and tests
- Local style inconsistencies with the file and surrounding codebase

## Quality Signals

### Good patterns to preserve

- Clear ownership: one module, component, or service has one main job
- Centralized invariants: shared types, schemas, normalization, and validation live in one place
- Simple control flow: direct branches, early returns, readable state transitions
- Type-safe interfaces: contracts are expressed in types instead of dodge-casts
- Derived state over repaired state: effects bridge external systems instead of bookkeeping local state
- Explicit states and transitions: loading, empty, success, failure, cancellation, retry, and cleanup behavior are coherent where relevant
- Scalable boundaries: split rendering, orchestration, shaping, and side effects when a file is clearly mixing them
- Performance-aware reactive code: avoid duplicate state, repeated heavy work, and unnecessary rerender or effect churn
- Useful comments: explain intent or constraints, not obvious code

### Bad patterns to report, or fix in edit mode when local and behavior-preserving

- Patch-on-patch growth: more guards, flags, branches, or effects layered onto already confused logic
- Mixed responsibilities: one file doing orchestration, transformation, rendering, persistence, and error handling at once
- Duplicated business logic: repeated normalization, model mapping, status derivation, or fallback behavior
- Repair logic instead of ownership: `useEffect` or watchers keeping state in sync after the fact
- Missing, duplicated, contradictory, or impossible states; stale state that survives retries, cancellation, navigation, or teardown
- Weak typing: `any`, unsafe casts, loose shapes, or ad hoc object contracts
- Cosmetic abstraction: more wrappers or helpers while the underlying flow gets harder to follow
- Hidden performance costs: effect spam, unnecessary recomputation, repeated expensive work, duplicated subscriptions
- AI-slop artifacts: needless comments, over-defensive code, placeholder prose, awkward helper names, and style drift

### Design-level patterns to catch

Use these as review signals, not automatic findings. Tie each claim to the changed code and current goal.

- Over-engineering the solution.
- Overly defensive programming.
- Hyper-fixating on rare or fictitious edge cases.
- Subsystems with overlapping responsibilities.
- Solving a problem at the wrong architectural layer.
- Duplicating sources of truth, then adding machinery to keep them synchronized.
- Prematurely generalizing a one-off flow.
- Adding timeouts everywhere without a concrete need to bound an operation.
- Adding production code that exists only to satisfy tests.
- Patching a bad premise additively instead of stepping back and deleting or replacing it.

## Type evidence (TypeScript / JavaScript)

On changed TypeScript and JavaScript hunks, also treat as slop any code that
discards or fabricates type evidence: the author knew the real type and hid
it from the compiler. Prefer inference, `as const`, `satisfies`, named owner
types, and parsing at the boundary.

In edit mode, fix directly when the edit is local and behavior-preserving. In review mode, report these findings:

- Assertion chains that fabricate evidence: `as object as User`, widening a known value to `unknown`, `object`, or an index signature and asserting it back, or annotating a known object as `Record<string, ...>` so its keys disappear.
- `unknown`, `object`, or `{}` standing in for a contract the author could have named, on parameters, returns, aliases, or dictionary values. An explicit error-`cause` convention is fine.
- `Reflect.get` or `Reflect.apply` where ordinary typed access works.

Route through the structural handoff when the real fix is not local:

- `vi.mock` / `jest.mock`-style module mocks that stand in for a missing dependency seam.
- Ad hoc `typeof` narrowing of unparsed input that should be parsed once at the boundary.

A truly necessary assertion stays. If it lacks a `SAFETY:` comment (or the repo's
equivalent) stating the invariant TypeScript cannot express, recommend that
comment in review mode or add it immediately above the assertion in edit mode. A placeholder justification, or a SAFETY comment laundering an
assertion that should not exist, is itself slop. Do not weaken, suppress, or
disable lint rules to pass.

## Behavior

1. Direct cleanup pass: report obvious slop in review mode; fix it in edit mode.
2. Small quality pass: identify small, clear, behavior-preserving improvements in the touched area. Apply them only in edit mode. Check names, comments, types, control flow, state transitions, ownership, tests, and local consistency where they apply. On TypeScript and JavaScript hunks, also apply Type evidence.
3. Structural triage: if the issue is larger than a local cleanup, do not do the big rewrite by default.

Before finishing, re-read changed hunks and confirm that every inventoried path
was handled.

## Placeholders (minor)

Real gaps are sometimes written as prose (`// later`, `// for now`, `// temporary`, “should eventually…”, empty stubs) **without** `TODO:`. When the comment clearly means deferred work, recommend one line `TODO: <specific next step>` so intent is grep-friendly and explicit. Apply that replacement only in edit mode. Skip if the code is done; leave existing `TODO:` or `FIXME:` that already read well.

## Escalate Instead of Rewrite

- Report the little things first in review mode; fix them first in edit mode.
- If the structural problem is larger than a modest local refactor, stop short of a broad rewrite.
- Append a compact handoff with:
- `Structural issue: ...`
- `Why it matters: ...`
- `Refactor direction: ...`
- `Follow-up prompt: ...`

## Guardrails

- Keep cleanup behavior unchanged. Report clear bugs separately; fixing one requires a separately authorized bug-fix scope, not just cleanup or commit authorization.
- Prefer minimal, focused edits and modest local refactors over broad rewrites.
- In edit mode, use behavior-preserving targeted restructuring when the boundary is obvious and it clearly improves the touched area. In review mode, recommend it without edits.
- Do not claim an exhaustive review when any target path or changed hunk was skipped. State the limitation instead.
- Keep the final summary concise (1-3 sentences) when no escalation is needed.
- Do not install dependencies, rewrite lint config, or expand a commit to vendor tooling. Type-evidence cleanup is in-diff only; plugin setup belongs to a later explicit request.
