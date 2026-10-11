---
name: efficient-implementation-plans
description: Write, review, or revise software implementation plans. Use for planning requests or reviews of planning and testing overhead, not ordinary coding.
---

# Efficient implementation plans

Make the requested outcome reliably achievable and done extremely well with proportionate product, planning, and verification effort. Every additional requirement, abstraction, test, or supporting system needs a concrete benefit for the current goal.

## Establish the outcome against the code

Read the current request, agreed constraints, existing plan, and relevant code before proposing replacements. For behavior changes, follow the relevant entry point through the components responsible for the result. Identify what already works and what must change. Investigate a named uncertainty instead of surveying the whole repository.

In the plan, distinguish verified capabilities from proposed mechanisms. For low-impact, reversible choices, a reasonable assumption or small experiment may be more useful than further investigation. A failed experiment can be useful if its effects are contained and its result informs the next decision. For a consequential uncertain capability, state the assumption and a proportionate check or exploratory implementation that can resolve it before dependent work relies on it. For example, a shared database does not establish that an existing email queue can join its transaction. Verify that capability before promising atomic insertion or budgeting the work as a handler-only change.

**Define an observable finish first.** State the delivered behavior, scope, relevant dependencies, and sufficient acceptance criteria. Separate binding outcomes and safety boundaries from suggested implementation details. Do not turn "robust" or "production-ready" into an unbounded list of hypothetical requirements. Prefer the simplest design that fully meets the current goal. Discuss the outcome with the user when it is still being shaped or a consequential choice is unsettled, so you are on the same page. Use agreement already established instead of asking for it again.

Match the requested action: discussion, review, drafting, or editing. Interpret follow-up questions in light of authorization already given. Discussion and review alone do not authorize file changes, implementation, publication, or changes to project rules. Follow existing project contracts; this skill does not authorize silently dropping agreed gates. Propose changes to binding outcomes, scope, or safety requirements for the user's decision. Make routine implementation and verification choices autonomously within the authorized scope. Keep independent work moving while a necessary answer is pending.

## Make ownership and dependencies concrete

Name the contracts that matter: inputs and outputs, state that must remain true, and meaningful failure behavior. At an authority boundary, one component owns a decision or state change. State which component owns it and what callers may assume. For example, a client requests approval; the server verifies the current revision and permission before the worker acts. A hidden button alone does not enforce that contract.

Prefer a small complete behavior through the layers it touches. Each implementation slice needs an outcome, real prerequisites, and sufficient proof. Put the uncertain integration early enough to change the design before surrounding work accumulates.

System boundaries and work assignments are different. A feature may cross several component owners and still belong in one reviewable PR. Split it when a contract, independent outcome, migration, or permission boundary makes a separate deliverable useful. Scale migration and release safeguards to actual users, retained data, and dependent systems. Where continuity is required, plan usable intermediate states. Otherwise, a direct replacement may be appropriate without compatibility layers or fallback infrastructure. Avoid dividing every feature into disconnected database, API, and UI tasks.

**Justify expensive tests and new infrastructure. Choose infrastructure for the work it enables.** Reuse existing tools when they fit the goal and simplify the work. Build or replace infrastructure when it better serves the goal. Compare setup and maintenance effort with the benefit to development, verification, or deployment at the project's current stage. A small deployment workflow or repeatable test scene may be the simplest way to get reliable feedback. Make substantial supporting work visible in the plan; do not hide a new testing system under "add tests."

For multiple PRs, state the base or prerequisite of each and the behavior its own diff adds. For parallel work, the plan names the independent scopes and integration responsibility. Follow the project's existing isolation and dispatch rules; a separate chat is not file isolation.

## Choose the cheapest sufficient proof

Development checks, targeted boundary checks, and coherent end-to-end acceptance are available layers, not three mandatory steps for every task. Judge risk by the actual effects of a change; a one-line permission change can affect many actions. Match each consequential acceptance claim to a check that could disprove it. Prefer a focused regression for bug fixes; a costly red baseline for a new feature requires a concrete purpose or explicit contract. Test a strict local invariant directly when it has a precise answer.

When the claimed outcome is a user journey, specify its normal entry point, destination, and observable result. An internal function call or prepared fixture proves only the boundary exercised. If credentials, hardware, or deployment access are missing, preserve that acceptance gap and continue the work that can be proved locally.

**Match verification effort to risk.** Ask what plausible incorrect implementation a costly test would catch that cheaper checks would miss. Choose variants for distinct risks and interactions, not an automatic Cartesian product of platforms, providers, and scenarios. Once the relevant checks pass, broaden or repeat them only for new changes, failures, or unresolved concerns.

**Keep the first working version focused.** For an early prototype, prioritize getting the smallest complete behavior working and evaluating it. Newly introduced hashing, evidence ledgers, or release machinery are a signal to revisit the current goal and implementation stage. In a small prototype they usually mean the steering is off. Leave them out unless a concrete requirement of that stage needs them. Use existing Git and test records for ordinary implementation work. Preserve safeguards for actual effects, and do not invent future risks to justify additional machinery.

For costly verification, put these conditions in the plan where the implementer needs them:

- **Rerun according to impact.** A result remains evidence for its tested artifact; assess its applicability to the delivered state using relevant source, dependencies, packaging, build, configuration, and environment changes. A reporter or selector change preserves unrelated evidence only if entry, fixtures, assertions, and success detection remain valid. Renew affected proofs and explain reuse briefly. Investigate uncertain impact and broaden verification where needed. A new revision alone does not require every previous live run.
- **Diagnose before expensive repetition.** Use the failure evidence to choose the next investigation. For example, you can classify failures as product, harness, infrastructure/external service, or access problems at the first failure. Narrow the reproduction when doing so preserves the relevant conditions; keep or broaden the scope when interactions matter. Another expensive run needs to answer a specific question, test a relevant correction, or follow a changed prerequisite. A likely transient failure may justify a bounded retry. Fix the real cause rather than stacking superficial patches, and verify the originally failing behavior -- and oftentimes it is at the smallest reproducible boundary.
- Verify the delivered state collectively. A selector fix cannot make an unreached assertion retrospectively pass. A successful retry establishes that run's outcome; retain original failures and retries when measuring reliability.

Use unexpected verification effort to reconsider the method, especially when further investigation is unlikely to change the next decision. Improve the approach autonomously within the agreed scope and communicate material deviations. Preserve agreed outcomes and safety requirements.

## Maintain one current plan

When plan edits are authorized, update the existing canonical plan in place. Replace superseded decisions and remove obsolete steps when requirements change. Preserve current constraints and the reason behind a non-obvious choice. Carry an old open question forward only if its answer affects the current outcome or next action. Keep execution details in existing task state, PRs, or test reports, with only the links needed to resume or assess the work. Do not prepend updates, preserve old plan versions inside the document, or move all removed prose into a new archive by default.

For a substantial task, a useful shape is:

- Goal and scope: the observable finish and the exclusions that prevent likely drift.
- Design: existing components, necessary changes, and consequential contracts or ownership.
- Implementation slices: outcomes in dependency order, with integration and PR boundaries where needed.
- Acceptance and open decisions: sufficient checks, material limitations, and prerequisites still needed.

Use only the sections the task needs. A small change can be a paragraph. Include verified source locations when they help the implementer start; avoid speculative file inventories and pseudocode that merely restates prose. Keep later stages at outcome level until their dependencies are understood. A plan's length follows its decisions, not a prescribed template or line count.

**Make reviews subtractive and finishable.** Resolve actionable ambiguities, contradictions, and relevant coverage gaps; remove redundant obligations and obsolete assumptions. A new mandatory requirement needs a current goal or concrete risk. Finish planning when the implementer can identify the next complete outcome, its prerequisites, and sufficient proof. Reopen settled decisions only with new information. Do not launch repeated whole-plan audits or additional reviewers without a specific unresolved question.
