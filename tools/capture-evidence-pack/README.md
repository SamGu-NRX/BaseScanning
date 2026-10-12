# Capture evidence pack

Bundles identity evidence for a bug report: which capture and which result the report is about, which schema versions govern them, hashes for every packaged file, which capture fields are explicitly missing, and the command to re-derive the receipt. It references capture inputs instead of embedding them, and it never claims the geometry is correct. The format is specified in [SPEC.md](SPEC.md).

```
fixture ──pack.py──▶ example.zip ──read.py──▶ PASS receipt, or REFUSAL <kind>
 (validate.py)                      │
                                    └─ serve.py calls read.verify_pack on every
                                       request; the page shows only its result
```

`pack.py` and `validate.py` are the producer. `read.py` shares no code with them, and the inspection server reads packs only through `read.py`.

## Pack the example

```sh
python tools/capture-evidence-pack/pack.py --fixture tools/capture-evidence-pack/fixtures/example --out tools/capture-evidence-pack/evidence/example.zip
```

Packing the same fixture twice to the same path produces identical bytes. [receipts/](receipts/) records the runs.

## Read a pack

```sh
python tools/capture-evidence-pack/read.py tools/capture-evidence-pack/evidence/example.zip
```

Prints a receipt and exits 0, or exits 2 with a named refusal and the SHA-256 of the bytes it refused. `--capture-root DIR` resolves the referenced files somewhere else.

## Inspect in a browser

```sh
python tools/capture-evidence-pack/inspection/serve.py
```

Serves a localhost page (default port 8793). For every request the server runs the reader on the pack and its referenced files, and the page shows the checked values, or the reader's refusal and nothing else. It also runs the reader against two mismatched capture roots and shows those refusals live. No external network calls; axe-core is vendored under `inspection/vendor/`, and `/?axe=1` runs it after both reads settle.

`--pack PATH` inspects another pack through the same check. `--fault`, `--delay-data` and `--delay-refusals` reproduce failed, malformed and slow reads for testing the page. Browser evidence for the page, and the harness that captured it, are in [inspection/evidence/](inspection/evidence/).

## Test

```sh
python -m pytest -q tools/capture-evidence-pack/tests
```
