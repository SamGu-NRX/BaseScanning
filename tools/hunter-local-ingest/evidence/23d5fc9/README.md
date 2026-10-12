# Receipts from the installed package at 23d5fc9

These four run directories come from the wheel built out of commit
`23d5fc9f8f47308b4ac69e1c0c85aa12434d111b`
(sha256 `90eeb7e4f89c42459c9bca3118db43e219f3fffccfb6e2ca20d8742811967ed7`),
installed non-editable into a scratch venv and run as console scripts from a
directory outside the source tree. The installed package's source digest,
`a3df65c9ef00ded7ec00a6a43d6d5f08d27154d147ee49af0837cb554cfc54bc`, equals the
digest of the committed `src/hunter_local_ingest`, and each manifest records it
as `client.source_sha256`. The plan is `hunter-gen-plan --seed 1` (plan file
sha256 `eabf28a3…692f`); it is not committed because the seed reproduces it.

| Run | Endpoint | Exit | Verdict | What it shows |
| --- | --- | --- | --- | --- |
| `selfcheck/` | in-process fake, fault round, no-op restart | 0 | PASS | 26 PASS rows labeled FAKE_HUNTER; GPU arms NOT_RUN |
| `real-api-unreachable/` | declared `REAL_API`, nothing listening | 4 | BLOCKED | an absent backend is 13 BLOCKED rows and a BLOCKED trace check, never a pass |
| `mutation-commits-tampered/` | fake that commits tampered bytes | 1 | FAIL | both tamper controls and the trace check fail; everything else passes |
| `canary-responses/` | fake that plants a marker in every response | 0 | PASS | the marker in `index.json` occurs 0 times in the run's files |

None of these runs touched a real backend, a GPU or a phone.

`index.json` lists every artifact's sha256, each chain head, the exit codes,
and the result of `hunter-verify-run` on the sealed originals (all OK). Git
checks files out writable, so on these copies `hunter-verify-run` also reports
`not sealed (writable)`; run `chmod -R a-w <run-dir>` first to compare the rest.
