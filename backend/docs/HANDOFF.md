# Handoff for an outside agent

This is for Codex, or any outside agent, that may assist or take over overnight. Use your judgment. Read `docs/BUILD_STATUS.md`, the open pull requests and their descriptions, and `docs/build/decisions.md`, then act.

## Hard rails

These are the only hard rails. Everything else in this file is guidance you may override when your judgment says so.

- No secrets and no key values. Env names only (`OPENAI_API_KEY`, `XAI_API_KEY`, Modal token names). Never print, log, or commit a value.
- No real meter numbers. No Base SSR prompt text.
- No force-push.
- No direct push to `main`.
- No `terraform apply`.
- No direct push to another agent's PR branch. Open a pull request into that branch instead.

## What you should do on your own

- Open your own `assist/<topic>` pull requests for bugs, gaps, missing tests, integration seams, or docs.
- Open a fix pull request that targets a stuck owner's branch, instead of pushing onto it.
- Run your own validations and post the findings as pull request comments.
- Pick up unclaimed work listed in `docs/BUILD_STATUS.md`.

Claim work before you start: open a draft pull request, or comment on the owning pull request. Prefer small pull requests.

`assist/*` pull requests use the same merge gate as the workstream PRs: an independent validator PASS at that head SHA, and green CI. A PASS does not carry to a later commit except under SD-80 (a merge of `main` plus conflict resolution only in `Makefile`, `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, or `server/tests/test_skeleton.py`, with `uv lock --check` and green CI). Do not wait on Bugbot (SD-79). Resolve review threads before merge.

## Roles

- **Builders** own one workstream each (WS-00 through WS-14) and the files in `docs/build/ws-*.md`. Their branches are `ws-XX/<kebab-topic>`.
- **Validators** re-run the brief's gate and report PASS or FAIL at an exact head SHA. On a later round, re-run the full gate only where the diff touches it (SD-85).
- **Integrator** squash-merges into `main` when the merge gate is met, and may resolve shared-file conflicts on a PR branch under SD-80 without mixing in feature commits.
- **Coordinator** keeps `docs/BUILD_STATUS.md` honest, routes review comments, and records seam decisions in `docs/build/decisions.md`. The Cursor coordinator does this while it is active.

`docs/build/decisions.md` wins over a brief when they disagree. Measured reports win over prose. The briefs name SSR files as forbidden sources and do not quote that wording.

## Where status lives

- `docs/BUILD_STATUS.md` — who is merged, who is open, what is unclaimed. Update it when a PR merges or a gate flips.
- `docs/INTEGRATION.md` — how to call the deployed API. When the deployment changes, edit the seven fields in **Current status** and add a changelog line.
- `docs/build/` — build plan (`server-pipeline-build-plan.md`), brief index (`README.md`), the fifteen briefs, and seam decisions (`decisions.md`).

## If the Cursor coordinator is silent

If the Cursor coordinator has been silent for 60 minutes or more, you become the coordinator. Run the merge gate yourself: validator PASS at the head SHA, green CI, then merge. Keep claiming work in the open (a draft PR or a comment) so two agents do not start the same fix.

While you are coordinator, still read `docs/build/decisions.md` before a merge, especially SD-77 (one environment), SD-79 (no Bugbot wait), SD-80 (shared-file conflicts), SD-82 (no new security-only work), and SD-86 (demo auth off). Trust the pull request body and the head SHA over an old line in `docs/BUILD_STATUS.md`.
