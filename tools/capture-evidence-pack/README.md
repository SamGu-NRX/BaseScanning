# Capture evidence pack

Bundles identity evidence for a bug report: which capture and which result the report is about, which schema versions govern them, hashes for every packaged file, which capture fields are explicitly missing, and the command to re-derive the receipt. It references capture inputs instead of embedding them, and it never claims the geometry is correct. The format is specified in [SPEC.md](SPEC.md).

## Pack the example

```sh
python tools/capture-evidence-pack/pack.py --fixture tools/capture-evidence-pack/fixtures/example --out tools/capture-evidence-pack/evidence/example.zip
```

Packing the same fixture twice produces identical bytes. [receipts/byte-stability.txt](receipts/byte-stability.txt) records a proof.

## Read a pack

```sh
python tools/capture-evidence-pack/read.py tools/capture-evidence-pack/evidence/example.zip
```

Prints a receipt. Exits non-zero with a named reason when a source ref or the result identity cannot be satisfied.

## Test

```sh
python -m pytest -q tools/capture-evidence-pack/tests
```
