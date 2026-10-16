# Backend: uploads behave the way the app's uploader expects over a real socket

## Why

Every upload test drives the volume routes through `TestClient`, which never drops a connection and never lets uvicorn's event loop multiplex requests. The app on the other end is a real HTTP client on a real network: `CaptureUploader` retries a dropped PUT on the **same** signed URL (a transport error is `Transient`; only a 403/410 goes back to register for a fresh one), resends sealed bytes once after a 400, and honors `Retry-After` on a 429. A phone can also abort a PUT outright, and the events long-poll keeps requests open while uploads run. None of that is exercised today; the Greptile review on #247 flagged exactly this area (parallel uploads to the same key).

This packet adds a TCP-level suite that runs `create_app` under real uvicorn on 127.0.0.1 and speaks raw HTTP to it, then fixes the two server-side defects the suite caught.

## The suite (fails-first evidence)

`backend/server/api/tests/test_uploads_tcp.py` — seven tests, one uvicorn subprocess per test, memory state, local volume storage, no cloud service and no real data:

- **aborted PUT** — raw socket sends half a body then RSTs. Must commit nothing, leave no temp file, and the retry to the same signed URL must succeed.
- **overlapping PUTs** — the first attempt holds a body open while a full PUT to the same URL lands, then finishes. Both must get 200 and the committed object must be exactly the sealed bytes.
- **completed-PUT replay** — the same URL PUT twice, both 200, commit acknowledges.
- **expired URL** — a PUT with an expired token is 410 `token_expired`, a GET-op token PUT is 403, nothing commits, and a re-registered URL commits.
- **wrong digests** — wrong `Content-MD5` header (400 `bad_digest`), wrong body bytes (400 `BadDigest`), short body (refused or dropped, never committed), declared `Content-Length` mismatch (400 `bad_digest` at `/headers/content-length`). Nothing commits, no temp files.
- **burst** — 8 concurrent PUTs racing 2 open event long-polls; every PUT finishes inside 25 s honoring 429 `Retry-After`, polls return inside their wait, all commits acknowledge, stored bytes match.
- **storage scope** — an `Authorization` header on a storage PUT is ignored, never required (`ScopedCaptureHTTP` refuses to send one; signed headers only).

**Run against unmodified alpha `4bcbedba`: 3 failed, 4 passed.**

- aborted PUT → FAILED: the temp file is left behind (leak).
- overlapping PUTs → FAILED: the still-open first attempt got **500 Internal Server Error**.
- short body → FAILED: the temp file is left behind (leak).

Replay, expiry, burst, and Authorization already behaved.

## Root causes and fixes

**1. One temp path per key, not per attempt — `server/common/backends/storage_volume.py`.**
`LocalFsStorage.prepare_tmp` returns `{key}.uploading`. When the phone retries a dropped PUT on the same signed URL while the first attempt's handler still drains its body, both attempts write the same file: the retry's `open("wb")` truncates it under the first attempt's handle, and whichever attempt commits second either moves the other's bytes or crashes on a vanished temp path — the observed 500 on valid bytes. `VolumeStorage` now overrides `prepare_tmp` to hand each attempt a unique file under `.uploads/singles/`, a prefix `list_prefix` already excludes from uploads listings. Each attempt commits its own complete bytes; the last commit wins, which is the replay rule.

**2. A dropped connection leaked the temp file — `server/api/routes/uploads.py`.**
An aborted or short PUT surfaces as `starlette.requests.ClientDisconnect` out of `request.stream()`. Only `_TooLarge` was handled, so the temp file survived and the slot was released with litter in the volume. The handler now discards the temp file on disconnect and answers 400 `BadDigest` (best-effort; the client is gone). Nothing is committed; the retry starts clean.

## Verification

- Focused suite: **7 passed** — 3 consecutive runs after the fix; failing-first 3/4 baseline recorded above.
- `ruff check` and `ruff format --check` clean on all three touched files.
- `make test` (full backend suite): counts below in "Full-suite result".

## Notes

- A re-register inside the same second returns a byte-identical signed token (the mint is deterministic at second granularity). Harmless for the app's flows — re-registers follow a 3600 s expiry or a 403, and a repeated token is simply PUT again — noted here so it does not read as a cache bug.
- A PUT whose client stalls mid-body without disconnecting still holds one of the four upload slots until the connection drops. No idle timeout was added: a per-chunk bound is a new threshold decision on a path where slow cellular uploads are normal, and the disconnect fix releases the slot promptly on RST/FIN, which is the case the app actually produces.
- Out of scope (reserved for other writers in burst 11): `get_file` naming, `realapi`/multipart/CI parity, the app's Swift client, `eval/gates.yaml`.

## Test plan

- [x] `uv run pytest server/api/tests/test_uploads_tcp.py` — 7 passed, 3 consecutive runs
- [x] `ruff check` + `ruff format --check` on touched files
- [x] Full backend suite — 1,469 collected: 1,432 passed / 19 skipped / 4 failed / 14 errors (counts below)
- [x] No new threshold or reserved-file edits; owns list respected

## Full-suite result

Full backend suite, 1,469 tests collected: **1,432 passed, 19 skipped, 4 failed, 14 errors** — zero failures outside the documented alpha baseline.

- **4 failed** — all four are `server/eval/tests/test_gates.py` cases (expired SD-96 fixture window); the same four the CI-proposal runner deselects by name.
- **14 errors** — all `tests/test_capture_cold_restart.py` collection failures (`load_receipt` import, broken on pristine alpha).
- **Reconciliation vs pristine alpha `4bcbedba`** (1,462 collected: 1,425 passed / 19 skipped / 4 failed / 14 errors): this head adds exactly the 7 TCP tests, all passing — 1,425 + 7 = 1,432. Nothing else moved.

Run mechanics: executed as eight parallel pytest chunks (1,469 collected IDs split into eight ~185-test groups, each a separate `uv run --frozen --extra api pytest` process) after two serial runs were killed mid-flight by sandbox checkpoint/restore. The known-slow C2 criteria test (~6 CPU-minutes, reproduced identically on a pristine-alpha worktree) sat in the criteria region; chunk wall times ranged 14s–6m45s.

---

Human author: Sam Gu (sgu07966@gmail.com)
