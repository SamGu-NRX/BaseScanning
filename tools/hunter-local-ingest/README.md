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

Runner exit codes: 0 all pass; 1 at least one FAIL; 2 endpoint unreachable /
BLOCKED; 3 bad invocation.

## Receipt contract

Each `checks.jsonl` line: `id`, `transport`, `oracle`, `expected`, `observed`
(sanitized), `status`, optional `evidence`, `ts`. The summary aggregates counts
by transport, carries lead questions and blocked notes. `summary.json` never
restate raw response bodies — digest prefixes (`body_sha256=…`) are the audit
trail.

## GPU arms

GPU-stubbed arms (`objects`, `vlm`, `dense`) are recorded as named NOT_RUN
checks. They execute only under explicit operator authorization; no run in
this package claims GPU, phone, or safety accuracy.
