# Handoff for an outside agent

This is for an agent (Codex or otherwise) that may assist or take over overnight. Read this file, then `docs/BUILD_STATUS.md`, the open pull requests, and `docs/build/decisions.md`.

## Roles

- **Builders** — one per workstream (WS-00 through WS-14). Each owns the files in that brief (`docs/build/ws-*.md`) and opens the workstream PR. A builder does not validate their own PR and does not merge it.
- **Independent validators** — a different agent from the builder. They re-run the brief's gate at the exact head SHA and report PASS or FAIL. A later round re-runs the full gate only where the diff touches it (SD-85).
- **Integrator** — squash-merges into `main` when the merge gate below is met. The integrator may resolve shared-file conflicts on a PR branch only under SD-80. The integrator does not add feature commits while doing that.
- **Coordinator** — keeps `docs/BUILD_STATUS.md` honest, routes review comments back to the builder, and records seam decisions in `docs/build/decisions.md`. The Cursor coordinator is the one who normally does this. If that process stops, the section "If the coordinator stops" is the takeover path.

## Merge gate

Merge only when all of these are true:

1. An independent validator's PASS is for the exact head SHA. A PASS does not carry to a later commit except under SD-80.
2. CI is green on that head.
3. Every review thread is resolved.
4. **SD-80 (conflict carry-over).** A PASS still counts on a new head when the only diff from the validated head is a merge of `main` plus conflict resolution limited to `Makefile`, `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, or `server/tests/test_skeleton.py`, `uv lock --check` passes, and CI is green on the new head. Keep both sides: every Make target, every CI job, every extra, every dependency; then re-lock. Do not mix a feature change into that merge commit. Builders should merge `main` themselves as soon as their PR conflicts, with the same keep-both-sides rule.
5. **SD-79 (do not wait for Bugbot).** Bugbot is not a merge condition. Validator PASS (or an SD-80 carry-over) at the head SHA, green CI, and resolved threads are enough to merge.

Nobody merges their own PR.

## Branches and rules of the road

Workstream branches are `ws-XX/<kebab-topic>` as named in `docs/build/README.md`.

- Never push to another agent's PR branch.
- Never force-push.
- Never push to `main`.
- Claim new work by opening a draft pull request from a branch named `assist/<topic>`. Say in the PR body which workstream you are helping and that you will not push to their branch.
- No `terraform apply`. Planning and reading Terraform are fine. Applying is not.
- No secrets beyond what the user grants for a named task. Env names only (`OPENAI_API_KEY`, `XAI_API_KEY`, Modal token names). Never print, log, or commit a value. Never commit a real meter number, Base SSR prompt text, or a file over 5 MB.

`docs/build/decisions.md` wins over a brief when they disagree. Measured reports win over prose. The briefs do not quote SSR prompt wording; they only name those files as forbidden.

## Where status lives

`docs/BUILD_STATUS.md` is the snapshot of who is merged, who is open, and what is blocked. Update it when a PR merges or a gate flips. It has no store links.

`docs/INTEGRATION.md` is the contract for agents calling the deployed API. Its **Current status** section is the live-deployment record. Update that block, in the same seven fields, whenever the deployment changes.

`docs/build/` holds the build plan (`server-pipeline-build-plan.md`), the brief index (`README.md`), the fifteen workstream briefs, and the seam decisions (`decisions.md`).

## If the coordinator stops

1. Read `docs/BUILD_STATUS.md` and this file.
2. Read the open PRs and their descriptions (`gh pr list` and `gh pr view`). Trust the PR body and the head SHA over an old status line.
3. Read `docs/build/decisions.md`, especially SD-77 (one environment), SD-79 (no Bugbot wait), SD-80 (shared-file conflicts), SD-82 (no new security-only work), and SD-86 (demo auth off).
4. Continue as a builder only on a new `assist/<topic>` draft PR. Do not push to an existing `ws-XX/*` branch. Do not merge. Leave merge to whoever is acting as integrator, and only through the gate above.
5. If you change the deployed API, edit the seven fields in `docs/INTEGRATION.md` **Current status** and add a changelog line. Do not deploy or apply Terraform as part of taking over unless the user explicitly grants that task.
