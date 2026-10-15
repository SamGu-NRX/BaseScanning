# Fold-in: E2E criteria on reconstruction

Source: `internal/breadth/E2E-criteria-on-recon.md`, landed 2026-09-26 22:02 UTC. The plan text still says one real house. This capture is two houses: real-001 expected pass, real-002 expected reject. Neither has been captured.

## Old to new

| Item | Old | New |
|---|---|---|
| Pass path | assumed exercisable on ETH3D or a walkaround | 102 photo-model verdicts, 87 scored: 0 unsafe passes, 0 false rejects, 0 missed reviews, and no labelled pass anywhere. `pass_recall` stays SKIP until real-001 labels exist |
| Site decision | a photo model can pass | 100% manual review on laser and on every photo model |
| Decidability | open | laser 38% / 75% (facade / electro); photo true scale 13% / 25%; ARKit-only 11% / 18%; meter-model 12% / 24% |
| C1 evidence rule | plan engine | decides 1–7% of cells on photo models; PR #11 decides 15–56% |
| ARKit 6% bound | a usable scale | turns decisive fails into unsure (facade C1 fail positions 73% to 10%). The 1.4% meter-model bound restores them (95%) |
| Walls | the fusion keeps them | edge-on walls w2/w3 go missing; π³ fusion can add a phantom wall |
| Priors | tight priors on synthetic drift | they lock onto −2.95% / +2.48% scale. Keep σ at 3 cm, mark synthetic rows informational, gate on the A6 LaMAR segments |
| Flips to watch on real-001 | none named | `opening_clearance` 10–19 positions and `headroom` 3–7 flipped fail to pass and never became a site pass |
| Close-ups | a stopped session is fine | `yt-LCPBf86oloE` registered 44/183 image-only; the blank close-ups did not register |
| Harness | schema max incidence 90 | a run reported 90.2. A reversed opening span crashed the PR #11 adapter |

## Owners

WS-04, WS-05, WS-06, WS-09, WS-11, WS-14, WS-00.

## Issues not opened

- `fold-in E2E: pass_recall stays SKIP until real-001 labels` (WS-11)
- `fold-in E2E: keep sigma 3 cm and gate on LaMAR segments` (WS-04)
- `fold-in E2E: meter-model bound restores fails` (WS-05)
- `fold-in E2E: edge-on walls and views_needed` (WS-06)
- `fold-in E2E: evidence-rule review` (WS-09)
- `fold-in E2E: keep ARKit running through the close-ups` (WS-14)
- `fold-in E2E: incidence max 90 and reversed opening span` (WS-00)
