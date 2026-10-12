# Receipts from the installed package at 1511668

These seven run directories come from the wheel built out of commit
`1511668e7e1df5d87228862b9d502708a10dee54`
(sha256 `029257adc3597d7f8364531bd86d71d16896fcb7709153ebaf7e255054932121`),
installed non-editable into a scratch venv and run as console scripts from a
directory outside the source tree. The installed package's source digest,
`843fd20eb408e430…`, equals the digest of the committed
`src/hunter_local_ingest` (full values in `index.json`). The plan is
`hunter-gen-plan --seed 1`; the seed reproduces it, so it is not committed.
The older `evidence/23d5fc9/` receipts describe that earlier source and are
unchanged.

| Run | Endpoint | Exit | Verdict | What it shows |
| --- | --- | --- | --- | --- |
| `selfcheck/` | in-process fake, fault round, no-op restart | 0 | PASS | 26 PASS rows labeled FAKE_HUNTER; GPU arms NOT_RUN |
| `real-api-unreachable/` | declared `REAL_API`, nothing listening | 4 | BLOCKED | 13 BLOCKED rows; an absent backend never passes |
| `mutation-commits-tampered/` | fake that commits tampered bytes | 1 | FAIL | both tamper controls and the trace check fail |
| `canary-responses/` | fake that plants a marker in every response | 0 | PASS | the marker occurs 0 times in the run's files |
| `rerun-committed-capture/` | second run of the same plan on one server | 0 | PASS | the committed baseline is replayed; the rerun's 10 PUTs all belong to fresh control captures |
| `max-batch-2/` | fake that enforces `upload.maxBatch` = 2 | 0 | PASS | lifecycle and tamper controls stay within two files per call |
| `auth-fault-arm-blocked/` | token-protected fake, `--with-faults`, token from `HUNTER_API_TOKEN` | 4 | BLOCKED | authenticated baseline passes; all 12 fault checks BLOCKED with `auth_fault_proxy_not_authorized`; 0 unauthenticated API requests; the synthetic token occurs 0 times in output or receipts |

None of these runs touched a real backend, a real credential, a GPU or a
phone. The token in the last run was a fresh synthetic value set only in
that subprocess's environment.

`index.json` lists every artifact's sha256, each chain head, the exit codes
and the result of `hunter-verify-run` on the sealed originals (all OK). Git
checks files out writable, so on these copies `hunter-verify-run` also
reports `not sealed (writable)`; run `chmod -R a-w <run-dir>` first.
