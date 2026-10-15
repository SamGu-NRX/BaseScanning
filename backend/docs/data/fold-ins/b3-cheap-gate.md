# Fold-in: B3 cheap gate

Source: `internal/breadth/B3-cheap-gate.md`, landed 2026-09-26 21:41 UTC. The store copy is `internal/build/fold-ins/B3-cheap-gate.md`. The plan was not edited.

## Old to new

| Item | Old | New |
|---|---|---|
| Gate | OWLv2 proposals, then crops, then a cheap tier | CLIP ViT-B/32 union SigLIP2-base, zero-shot, top-2 frames per equipment class, no crops, full frame 2048 px |
| OWLv2 | candidate gate | not a gate (about 13× slower, no gain) |
| Confirm | unspecified cheap confirm | GPT-5.4-mini presence, then GPT-5.5 confirm; gas-meter and pool picks skip mini |
| VLM cost | about $2.32/house; gate-decision band $0.75–1.65 | about $0.30/house; WS-07 ceiling $0.85 stays |
| Meter fill | auto-fill if agreement is at least 95% | 68/73 agree, 65/68 correct, Wilson low 87.8%; no auto-fill without a zxing barcode match; pre-fill top-3 with one-tap confirm |
| Panels | open | mini at 2048 px first; escalate hazard, unprinted brand, amps, or legible-but-no-amps to GPT-5.5; 0 false hazard alarms |
| Reading gate | open | SigLIP linear probe kept 208/208 |

## Owners

WS-07 (cascade), WS-12 (cost line). WS-08 already carries the meter and panel rules; confirm only.

## Issues not opened

- `fold-in B3: CLIP union SigLIP2 cascade, no OWLv2 gate` (WS-07)
- `fold-in B3: VLM cost about $0.30 per house` (WS-12)
- `fold-in B3: no meter auto-fill without a zxing match` (WS-08, confirm)
