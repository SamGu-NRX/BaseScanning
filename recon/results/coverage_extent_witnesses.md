# Coverage-extent witnesses: baseline, pipeline witnesses, schema receipts

The record of the witness pass on top of ea38351 ("Coverage stops at the fitted extent and
samples the fitted ground"), which fixed the two coverage defects the handoff files as issues
#95 and #96. Everything here ran offline on synthetic bundles: no real home capture, no private
rule, no depth model, no server call.

## Baseline (head ea38351, the three-file repair untouched)

Run in `recon/`, 2026-10-10:

| Command | Result |
| --- | --- |
| `uv sync --locked` | Resolved 17 packages; environment up to date |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 20 files already formatted |
| `uv run pytest -q` | 90 passed, 50 warnings, 11.7 s |
| `uv run pytest -q tests/test_coverage_fit.py` | 9 passed, 1.8 s |
