# hunter-local-ingest

Runs the published HouseScanKit upload lifecycle against one capture-API
endpoint and records what happened. It creates a capture, registers and
uploads files, commits them, finalizes with a packet, follows events to a
terminal status and reads the result, then runs controls that tamper with
uploads, replay a create and read a capture that does not exist. Every check
ends PASS, FAIL, BLOCKED or NOT_RUN and names how it ran.

The request bodies, response types, retry rules and header handling come from
`ios/HouseScanKit/Sources/HouseScanKit` at alpha `263827ef`
(`Upload/CaptureAPI.swift`, `Upload/CaptureUploader.swift`,
`Upload/ScopedCaptureHTTP.swift`, `CaptureResult/CaptureResult.swift`,
`Processing/FixtureCaptureHTTP.swift`). No backend code, schema or test is
copied here.

## Install and run

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'   # development
python -m venv .venv && .venv/bin/pip install .             # what a user gets

hunter-gen-plan --seed 1 --out plan.json        # or: python fixtures/gen_synthetic_packet.py
hunter-local-ingest --endpoint http://127.0.0.1:8000/v1 --plan-file plan.json \
    --backend-mode REAL_API --backend-ref <sha> --out runs --with-faults
hunter-verify-run runs/<run-dir>                # re-check a finished run on its own
hunter-selfcheck                                # everything above against the in-process fake
pytest -q
```

`--restart-argv-json '["cmd", "arg"]'` adds a restart-recovery check: the
harness runs the command without a shell, waits for the endpoint, and reads
the baseline capture again. Without it that check is NOT_RUN.

Exit codes: 0 every executed check passed; 1 a check failed; 2 usage error;
3 the plan was refused; 4 nothing failed but something was blocked; 5 the
receipts themselves failed (schema, privacy screen, missing file, integrity),
which outranks the others.

## What a label means

- `REAL_API`: the client exchanged HTTP with an endpoint the operator declared
  real. The harness cannot see whether the backend ran its CPU stages, which
  models it used or which ref it is; the summary always records
  `real_mirror_cpu: NOT_ESTABLISHED` and `backend_verified: false`.
- `FAKE_HUNTER`: the in-process fake (`fakeapi.py`) or the fault proxy
  (`faultproxy.py`). It shows the client reacts to a condition and is never
  evidence about a backend.
- `GPU_STUB`: the `objects`, `vlm` and `dense` arms, named and always NOT_RUN.

BLOCKED means the check got no answer or depends on a step that did not
complete, and NOT_RUN means it was deliberately skipped. Neither counts as a
pass, and the frozen-trace check reports such rows as not comparable rather
than agreeing. A request the client refuses to send (see below) is FAIL.

## What the client refuses

Every API request's final URL passes the published scope rule
(`ScopedCaptureHTTP`) before any header, token or connection: same scheme,
host and port as the base, under its path, with no dot segment and no
encoded `/`, `\` or `.`. A server-minted capture id such as `../outside`
therefore becomes a refused request (`auth_destination_refused`), and
nothing is sent. Storage PUTs use their own rule: https, or http to an exact
loopback host, with exactly the signed headers and never `Authorization`.

Register and commit go in batches of `min(10, upload.maxBatch)`, the
published uploader's limit, in the lifecycle and in the tamper controls
alike. Each commit answer is judged as it arrives, by the scenario's own
rule (the lifecycle needs every path committed; a tamper control needs its
victim kept out and every intact file committed), and the first answer that
breaks it ends the round as FAIL before another batch is sent. When register reports a file already committed (a replayed or resumed
capture), the client neither uploads nor commits it again, and both checks
record that committed-at-register set explicitly.

Published Swift `Int` fields (event `seq` and `next`, `etaS`, upload limits)
decode as 64-bit integers by value, as Foundation's JSONDecoder does: `5`,
`5.0` and `1e3` are all integers, and a non-integral value such as `5.1` is
refused. The response parser keeps decimal literals exact, so
`4611686018427387905.0` decodes to exactly that integer, never to a rounded
float. A value too large for a
receipt to hold as an exact JSON number (2^53 and up) is recorded as
`large-int:<digest>`, never clamped and never rejected. `Double` fields accept
any JSON number that converts to a finite double.

## The API credential

`--api-token-env` makes the runner read a bearer token from the
`HUNTER_API_TOKEN` environment variable. Without the flag the variable is not
read. There is no flag that takes the token itself: `--api-token` is refused
in every spelling, without echoing its value, because a command-line value
shows up in process listings and shell history.

Supply the variable through a secret manager that sets it in the runner's
process environment, not a literal assignment typed into a shell, which
history keeps. The environment is still readable by the same user and by
root; nothing here protects against them.

The whole token is checked (RFC 6750 b64token, so a trailing newline, a
carriage return, a space or any control character is refused) before any run
directory or request exists. It goes only on API requests to `--endpoint` whose final URL passes
the scope rule, never on a storage PUT and never to the fault proxy. Restart
commands and git run without it in their environment. Receipts record only
the flag name.

With a token, `--with-faults` does not run the fault round: every fault check
is BLOCKED with the reason `auth_fault_proxy_not_authorized`, and the run
exits 4 unless something failed. The proxy is never given the credential,
and its requests sent without one would only read as endpoint failures. So a
token-protected API gets no fault coverage from this harness; that gap is
open, not passed. Without a token the fault round runs as before.

## The synthetic plan

`fixturegen.py` writes seeded noise files, two seeded CSV streams, the
published `CreateRequest`, and a Packet04 packet whose `source.kind` is
`synthetic` with no keyframes. A different seed changes every file's bytes.
The runner's loader (`plan.py`) refuses any plan whose digests do not
recompute from the bytes it read, whose paths are not the generator's, or
whose create body or packet differ from the published shapes. Each control
and fault check uses its own derived packet id carrying this run's id, so
none of them can land on the baseline capture, and a rerun against the same
server opens fresh control captures while the baseline is a true replay.

## Receipts

Each run gets a new directory (exclusive `mkdir`, random run id) holding
exactly five files:

| File | Contents |
| --- | --- |
| `run-manifest.json` | declared backend mode and ref; digest of the package source that ran, plus git HEAD and a digest of the uncommitted diff when run from a checkout; fixture digests; the command with every value rendered by type; runtime; settings |
| `checks.jsonl` | one line per check: id, transport, status, the oracle and expectation (package constants looked up by id), and the observed record |
| `summary.json` | verdict, counts, failed/blocked/not-run ids, digests of the manifest and checks |
| `privacy-screen.json` | the pattern screen's findings over the three files above, run after the summary exists |
| `privacy-chain.jsonl` | a SHA-256 chain over every artifact's final bytes |

Values reach these files only through typed schemas (`export.py`). A string
must be a published enum word, a package constant, a digest, or a path or
packet id the plan declared. Server identifiers become `id:<12 hex>`, unknown
words `unrecognized:<12 hex>`, and unexpected paths and keys become digests,
so a disagreement stays visible without its content. Homeowner messages,
prompts, signed URLs and raw bodies are never recorded. Recorded commands
keep this program's flags typed (an endpoint as `host:port` only when it is
loopback, paths as `<path>`) and turn every other value into `<value>`, or
`***` after a credential-named flag. A value outside its schema stops the run
with exit 5 before it is written.

The pattern screen (`sanitize.py`) is the second layer. It cannot recognize a
bare word-shaped secret, which is why the schemas come first.

What the integrity check proves: the chain, the read-only modes and
`hunter-verify-run` detect an edit made after the run, such as a changed
byte, a broken link, a writable, extra or missing file. They do not stop the
directory's owner from rewriting a file, recomputing the chain and sealing
it again. The chain head is printed when the run ends; recording it
somewhere the run cannot write is the only anchor. A file `hunter-verify-run`
cannot read, decode or parse, or one of the wrong JSON shape, is reported by
file name and category with exit 5; its content is never printed. Screen
findings never delete evidence: failed artifacts survive, sealed, for
inspection. Sealing and a clean screen make receipts tamper-evident, not
publishable — the guarantees above govern when a receipt may leave the
machine.

## Layout

| Module | Role |
| --- | --- |
| `netpolicy.py` | the one rule for which hosts are loopback and which URLs may be used |
| `contract.py` | published vocabularies and one strict decoder per response |
| `http_client.py` | the client: transient retries, API-only credential, exact signed PUT headers |
| `scenarios/` | lifecycle, controls, fault round; the only place PASS/FAIL is decided |
| `scenarios/oracles.py` | every check id with its oracle and expectation |
| `traces/` | frozen expectations; v3 is current (v2 plus the committed-at-register branch); v1 and v2 are kept unedited |
| `export.py`, `receipts.py`, `verify.py` | schemas, writing and sealing, independent re-check |
| `fakeapi.py`, `faultproxy.py` | FAKE_HUNTER plumbing with knobs to misbehave |
| `provenance.py` | source digest, git state, runtime |
