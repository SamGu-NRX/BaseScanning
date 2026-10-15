# Builder brief template (one file per workstream)

Purpose: a Grok 4.7 builder agent with no prior context must be able to build the workstream and prove it works from this brief alone, and a second agent must be able to validate it independently. Write for a careful but literal reader: exact paths, exact signatures, exact commands, exact thresholds. No motivation essays. No placeholders like `<your path>`; use the real path or say how to derive it.

File name: `docs/build/ws-XX-<kebab-name>.md`. Length 150–350 lines. Code blocks at most 25 lines each (signatures, schemas, commands), never full implementations.

Required sections, in this order:

## 0. Card
- Workstream id, name, one-line goal
- Builder model: Grok 4.7. Validator: a separate agent (any model), never the builder
- Inputs consumed (artifact names + producing WS) and outputs produced (artifact names + consuming WS)
- Repo: `huntertcarver/house-scanning-server`, branch naming `ws-XX/<kebab-name>`, one PR per WS unless the brief says otherwise

## 1. Start gate
What must already exist before work starts (exact paths, merged PRs, fixtures). If the gate is not met, the builder stops and reports instead of inventing the missing piece.

## 2. Files you own / must not touch
Two lists of exact paths (create or modify vs. forbidden). Shared files (contracts, schemas, CI) are owned by WS-00 unless stated.

## 3. Interfaces
Exact Python signatures (module path, function or class names, argument and return types), pydantic models or JSON fields with types, units (metres vs inches, microseconds vs seconds), coordinate frames, error types and codes. Point to the contract docs (`docs/scene-and-verdict-format.md`, `docs/capture-packet-spec.md`, `docs/hosted-architecture.md`) for the full schema and restate only what this WS reads and writes.

## 4. Behaviour spec
Numbered steps. Every threshold, default, timeout, and retry count as a literal. Edge cases and failure modes with the exact error code or status each maps to. Anything non-deterministic (model calls, GPU) gets a seed or a tolerance.

## 5. Reuse
Exact source paths to port (store `internal/...` or repo paths), what to keep, what to drop and why. Licence notes when relevant (no AGPL, no non-commercial models).

## 6. Fixtures and data
Exact paths, how to fetch or regenerate, approximate sizes, which fixture exercises which branch (LiDAR vs image-only, pass vs fail cases).

## 7. Commands
Copy-pasteable: environment setup, run on a fixture, unit tests, lint. State expected runtime and the machine (CPU laptop vs Modal GPU).

## 8. Done when
Acceptance tests as commands with expected outputs or numeric thresholds. Include the regression gate that must stay green (0 unsafe passes, schema validation, harness scores). A reviewer must be able to run these without reading the code.

## 9. Validator checklist
How the second agent verifies: commands to run, artifacts to inspect, what counts as fail, what to sample by hand (e.g. open 3 rendered coverage maps), and the report format to file at `internal/build/validation/ws-XX.md`.

## 10. Constraints
Always include: vision models are GPT-5.5 and Grok 4.7 only (no Anthropic); never commit real meter numbers, Base SSR prompt text, API keys, or files over 5 MB; no customer tools (no tape measure) in any capture assumption; cost caps that apply to this WS (VLM calls per house, Modal spend); privacy of customer photos.

## 11. Out of scope and handoffs
What belongs to other workstreams, with their ids, so the builder does not drift.

## 12. Open questions and defaults
Each open question with the default to assume if unanswered, so work never blocks on a person.
