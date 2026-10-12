# hunter-local-ingest

A real-client evaluation harness for the local Hunter capture API. It drives the
published HouseScanKit client behavior (create → register → signed upload →
commit → finalize → events → result) against a live local endpoint, decides
PASS/FAIL/BLOCKED/NOT_RUN per check, and writes sanitized receipts.

The frozen public surface is the adapter protocol in
`src/hunter_local_ingest/adapter.py` (`AdapterInterface` v1). Backends are
exercised through that interface only; nothing here imports backend code.

## Privacy posture

Everything the harness sends and records is synthetic by construction:

- Fixtures are seeded noise under `fixtures/` (`gen_synthetic_packet.py`); the
  same seed reproduces the same plan byte for byte.
- Receipts carry statuses, field names, digest prefixes, timings, synthetic ids
  and allowlisted scalar values only. No private source, schemas, specs, raw
  logs, signed URLs or credentials appear in any receipt.
- A self-check sweeps its own receipts for leaks (`https://`, `Signature=`,
  `Authorization`, raw content) before reporting green.

## Layout

- `adapter.py` — `AdapterInterface` v1 + sanitized `Observation`/`StepResult`.
  `context` is transient and never serialized.
- `http_client.py` — the direct HTTP client implementing the interface.
- `modes.py` — `Transport` (REAL_MIRROR_CPU | FAKE_HUNTER | GPU_STUB) and
  `Status` (PASS | FAIL | BLOCKED | NOT_RUN).
- `scenarios/` — the only place PASS/FAIL is decided. Each check's oracle names
  its public derivation; expectations refined from local observations are
  marked `provisional` until a real backend baseline confirms them.
- `controls.py` — negative controls: idempotent create replay, corrupt upload,
  truncated upload, wrong-capture isolation.
- `faultproxy.py` — labeled FAKE_HUNTER fault injection (latency, canned
  statuses, truncation, aborts) with `harness_*` codes that can never collide
  with published contract codes. Fault results never substitute for a passing
  real check.
- `receipts.py` — `run-manifest.json`, append-only `checks.jsonl`,
  `summary.json`. Run directories are never overwritten or merged.
- `traces.py` + `traces/expected-lifecycle-v1.jsonl` — versioned, frozen
  expected traces (status class + required field names, subset semantics).
  New contract versions get a new file; v1 is never edited.
- `selfcheck.py` — in-process plumbing fake (explicitly NOT backend evidence)
  that exercises the full lifecycle, controls, fault round, receipt writing,
  trace comparison and leak sweep.
- `runner.py` — the CLI entry point against a live endpoint.

## Install

Two supported installs:

```bash
# repo-prescribed editable install (development; unit tests use this)
python -m venv .venv && .venv/bin/pip install -e '.[dev]'

# non-editable install (what a downstream user gets)
python -m venv .venv && .venv/bin/pip install .
```

A non-editable install ships the frozen traces as package data (the
`traces/*.jsonl` glob in `pyproject.toml`), so `hunter-selfcheck` can run
without the repo checkout once the trace lives under the installed package.
Status gate:
`tests/test_packaging.py::TestInstalledSelfcheck::test_installed_selfcheck_exits_zero`
pins that end state; at this head it is xfail because the self-check still
resolves the trace only at the tool root (`parents[2]`), which a
non-editable install does not have — that loader change is coordinated
separately. The installed `hunter-local-ingest` runner has no such dependency
and works from a plain install today (the packaging tests prove it over
subprocesses). The fixture generator stays a repo script: the installed
package reads only plan files passed with `--plan-file`.

## Usage

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'

# 1. deterministic synthetic plan
python fixtures/gen_synthetic_packet.py --seed 1 --out runs/fixtures/synthetic-plan.json

# 2. run against a live local endpoint (…/v1 base URL)
hunter-local-ingest \
  --endpoint http://127.0.0.1:8000/v1 \
  --plan-file runs/fixtures/synthetic-plan.json \
  --backend-mode REAL_MIRROR_CPU --backend-ref <sha> --client-ref <sha> \
  --seed 1 --out runs --label real-local --with-faults

# 3. plumbing self-check (no endpoint needed)
hunter-selfcheck --out runs/selfcheck

# 4. unit tests
pytest -q
```

Runner exit codes: 0 all pass; 1 at least one FAIL; 2 usage/argument error
(argparse) or a BLOCKED run (e.g. unreachable endpoint); 3 the plan file does
not match the generator schema. Plan-file diagnostics are converted to the
exit code and are not currently printed.

## Receipt contract

Each `checks.jsonl` line: `id`, `transport`, `oracle`, `expected`, `observed`
(sanitized), `status`, optional `evidence`, `ts`. The summary aggregates counts
by transport, carries lead questions and blocked notes. `summary.json` never
restate raw response bodies — digest prefixes (`body_sha256=…`) are the audit
trail.

Mechanics, per run directory:

- `run-manifest.json` — label, declared backend mode, refs, fixture identity
  and seed, and the full command line. Written once, before any check runs.
- `checks.jsonl` — append-only, one JSON object per check, flushed per line;
  a second writer appends, never truncates.
- `summary.json` — counts by transport, lead questions, blocked notes and
  notes. Written once at the end of the run; an existing summary is never
  rewritten (the writer refuses and points at a fresh `--out`).
- `privacy-chain.jsonl` — the digest chain over every receipt file, appended
  at each write and sealed at summary time (see Integrity below).
- `privacy-screen.json` — the post-write privacy screen's outcome: files
  screened, findings, clean flag.

Run directories are second-resolution stamps under `--out` and are never
overwritten or merged: a name collision refuses to run (exit 3), and absent
`--out` parents are created. Observations never carry raw response bodies —
digest prefixes, field names and allowlisted scalar values only.

## Integrity: tamper checks, not immutability

At summary time the run is screened, chained and sealed — and all three are
detection mechanisms only:

- Every receipt digest is linked into `privacy-chain.jsonl` (each entry binds
  the previous chain value, so editing or reordering any covered file — or
  the chain — shows up on replay), the post-write privacy screen's outcome is
  recorded in `privacy-screen.json` (findings never delete evidence: failed
  artifacts survive, sealed, for the operator to inspect), and every receipt
  file is then set read-only (`chmod 0444`).
- `verify_run_integrity(run_dir)` in `receipts.py` replays the chain and
  flags chain breaks, files modified after their entry, chained-but-missing
  files, files with no chain entry, and files left writable. It is not yet
  wired into any CLI flag or self-check step; call it directly on a run dir.

What these checks cannot do — the honest limits:

- Read-only permission bits are advisory: the file owner, root, or any later
  `chmod` can still modify or delete the files. Sealing is a guard rail
  against accidental edits, not a lock.
- The chain detects later modification; it cannot prevent it. It binds
  content after the fact and says nothing about who wrote a file or whether
  it was honest at creation time — the writer process controls every receipt
  while producing it, before any seal.
- The chain file lives in the same run directory under the same owner as the
  receipts it covers. A process able to rewrite the receipts can rebuild a
  coherent chain just as easily. The machinery assumes the creating process
  and detects independent, later tampering (a second process, manual edits,
  partial writes); it is not a defence against the writer itself.
- Sealing and a clean screen make receipts tamper-evident, not publishable.
  The privacy posture above governs when a receipt may leave the machine.

Known gap, hardening in flight: a receipt that was never created (a missing
`run-manifest.json` or `checks.jsonl`) is skipped by the post-write screen
and unflagged by the verifier today. A pending privacy change will make a
missing receipt a named, non-green finding in both the screen and the
verifier.

## Label vocabulary (what a receipt row may claim)

Every check row carries a `transport` label:

- `REAL_MIRROR_CPU` — the operator's declaration for the endpoint under test
  (`--backend-mode`). The harness never verifies the declaration: run against
  any endpoint with `--backend-mode REAL_MIRROR_CPU` and every main-round row
  claims REAL_MIRROR_CPU. The label records what was declared, not what the
  endpoint is.
- `FAKE_HUNTER` — the self-check's plumbing fake and the `--with-faults`
  fault-proxy round. These rows are never evidence of real backend behavior,
  and fault results never substitute for a passing real check.
- `GPU_STUB` — named arms (`objects`, `vlm`, `dense`) recorded NOT_RUN; they
  execute only under explicit operator authorization.

Statuses are PASS / FAIL / BLOCKED / NOT_RUN. The summary aggregates counts
per transport, so a fake-backed run is always distinguishable from a
real-declared one by its rows.

## GPU arms

GPU-stubbed arms (`objects`, `vlm`, `dense`) are recorded as named NOT_RUN
checks. They execute only under explicit operator authorization; no run in
this package claims GPU, phone, or safety accuracy.
