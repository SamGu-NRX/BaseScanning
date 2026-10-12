# Focused receipts from the installed package at aa10463

Three runtime conditions, run with the wheel built out of commit
`aa10463a66bddf1869ec975fc28fdb8991d9a388`
(sha256 `0584da030bd6075ef69b0c884e8478dcd1b8c0779f0e996cdfe91b29d15b3206`),
installed non-editable and run as console scripts outside the source tree.
The installed source digest equals the committed tree's (`index.json`). This
is not the full seven-run campaign: `evidence/1511668/` and `evidence/23d5fc9/`
remain the receipts for their own sources and are unchanged.

| Run | Exit | What it shows |
| --- | --- | --- |
| `commit-batch-semantic-fail/` | 1 | the first commit batch answers one path notFound; the lifecycle commit FAILs after that one batch, and the run is FAIL, not BLOCKED, although the next commit request gets no answer |
| `bearer-trailing-lf` (no directory) | 2 | a synthetic token ending in a newline is refused with `credential:auth_credential_malformed` before any run directory or request: no directory, 0 requests, the token in no output |
| `decimal-cursor-exact/` | 0 | the server writes an event cursor above 2^53 as a decimal literal; the next poll asks for exactly `after=4611686018427387905`, never the rounded `...904` |

The token run writes no receipts by design, so `index.json` records its exit
code, request count and output check instead. Fake servers only; no real
backend, credential, GPU or phone. `hunter-verify-run` reported OK on both
sealed originals; on these git copies run `chmod -R a-w <run-dir>` first.
