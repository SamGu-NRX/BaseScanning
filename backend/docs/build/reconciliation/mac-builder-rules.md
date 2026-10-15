# Rules for builders running on hunter-mac

Cloud agents cannot reach `huntertcarver/house-scanning-server` (GitHub returns 404 for the cloud token), so every builder and validator runs on Hunter's Mac with his `gh` login. Up to 13 agents share this machine with Hunter; these rules keep them out of each other's way.

## Paths

- `$S` (Project store) = `/Users/huntertcarver/Library/Application Support/Cursor/AgentStores/cursor_agent_stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/files` (cloud form `/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2`). Quote it; it contains spaces.
- Repo checkout: `~/house-scanning-server`. Never work in it directly. Never touch `~/house-scanning`, Arc or any browser, other agents' worktrees, or other agents' processes. No computer use.

## Worktree and branch

1. `git -C ~/house-scanning-server fetch origin`
2. `git -C ~/house-scanning-server worktree add ~/hss-wt/<ws-XX> -b <branch from your brief's Card> origin/main` (if the branch exists on origin, check it out instead).
3. Work only inside `~/hss-wt/<ws-XX>`. When `main` moves, `git merge origin/main` into your branch; never rebase a pushed branch, never force-push, never push to `main`.

## Tooling

- `uv` for Python (`brew install uv` if missing), Python version per `pyproject.toml`; `uv sync` inside your worktree only.
- Terraform, Modal CLI, `gh` are already configured by Hunter; use them, never print tokens or config files.
- CPU etiquette: at most one heavy local job at a time per agent (COLMAP, torch inference, full test matrix), run it under `nice -n 10`, and prefer Modal for GPU work. Stop background processes you started before you finish.

## Start gate

If the only unmet item in your brief's §1 is WS-00 code not yet on `main`, do not stop: build against the WS-00 brief's interfaces (`$S/docs/build/ws-00-skeleton-and-contracts.md` §3), keep your PR as a draft, and when WS-00's contracts PR merges, merge `origin/main`, fix anything that differs, and mark the PR ready. Any other unmet item: use the fallback your brief names (for example the volume storage backend when GCP is not ready); stop and report only when no fallback exists.

## PRs and merging

- Before every push, run the repo's safety scan script under `scripts/` (added in PR 1) and `uv run ruff check` plus the tests your brief lists.
- `git push -u origin <branch>`, then `gh pr create --draft --base main --head <branch>` with the §8 gate table (expected vs observed) in the body. Mark ready with `gh pr ready` when your §8 table is all PASS and your start gate is met.
- Never merge your own PR. An independent validator files `$S/internal/build/validation/<ws-XX>.md`; the integrator squash-merges when that verdict is PASS for your head commit and CI is green. If the validator fails you, fix and push; the coordinator re-runs validation.

## Secrets and spend

- Env names only (`OPENAI_API_KEY`, `XAI_API_KEY`, Modal tokens); never print, log, or commit a value. Without keys, use recorded fixtures or mocks and keep `VISION_EXTERNAL_CALLS=off` paths green.
- Modal spend: your brief's §10 cap; the workspace total is $200. GPU runs only when a §8 item needs them.

## Store writes

You may write only `$S/internal/build/layout-decisions-<ws-XX>.md` (append your new `[default chosen]` items) and `$S/internal/build/progress/<ws-XX>.md` (short status: branch, PR link, gate table state, blockers). Never edit briefs, the plan, contract docs, or another workstream's files.
