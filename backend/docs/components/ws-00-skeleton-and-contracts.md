# Component: WS-00 skeleton, contracts, fixtures

## Purpose

Typed contracts for every artifact that crosses a workstream boundary, the golden fixtures behind `make validate`, and the fetch path for the tars and PLYs that stay out of git.

## Owner WS and brief

WS-00. Brief: `docs/build/ws-00-skeleton-and-contracts.md`. This page covers the fixtures PR (`ws-00/fixtures`).

## Inputs / outputs (artifact names)

Inputs are the store copies under `internal/eval-harness/`, `internal/criteria/labels-v0/`, `internal/data/packets/archives/`, and `internal/packet-generator/schema/`. Outputs:

- Committed: `data/fixtures/scenes/`, `labels/`, `verdicts/`, `reference-scores/`, `packets/<house>/packet.<variant>.json`, `packets/mini-arkit/packet.json`, `packets/examples/`, `manifest.json`.
- Fetched into `.fixtures/` (gitignored): `gt/<site>/gt-laser/{laser.ply,occluders.ply}`, `packets/<house>.tar` plus the extracted `<house>/` tree, recon `points.ply`.

`make fixtures` never writes fixture payloads into `data/`. After a PLY lands in the cache it adds a gitignored symlink beside the committed scene, because `score-geometry` resolves `assets[].path` relative to `scene.json`.

## How to run on a fixture

```bash
make fixtures ONLY=small
make fixtures ONLY=all
make fixtures ITEM=yt-12Ks1idWj40
make validate
uv run python -m server.contracts.cli check-packets data/fixtures/packets/mini-arkit/packet.json --files
```

`FIXTURE_SOURCE` defaults to the store `internal/` directory. Set it to `gs://hss-fixtures` for the same relative layout when that bucket exists. `--item` names one manifest id and overrides `ONLY`.

Packet folders resolve `packet.json` first. A folder that also contains `packet.<variant>.json` is an error. `--variant` on a `packet.json` folder is an error. Otherwise the variant defaults to `arkit-nonlidar`.

## How to test

`make validate` checks every committed scene, verdict, labels file and packet manifest. It does not hash tar contents. `pytest` covers packet-folder resolution and the fetch exit codes (missing source exits 2, sha256 mismatch exits 3 and deletes the cache file).

## Rebuild from scratch (no VM-only state)

```bash
uv run python scripts/make_mini_packet.py --out data/fixtures/packets/mini-arkit
make gt    # ETH3D download, build_gt, copy PLYs into .fixtures/ and the store
make fixtures ONLY=all
```

`make gt` diffs the rebuilt `scene.json` against the committed one, ignoring `provenance.runtime_s`. If `walls`, `chain`, `objects` or `coverage.bands` differ, the committed scene stays and only `laser.ply` and `occluders.ply` are copied. It then copies those two PLYs into `$FIXTURE_SOURCE/eval-harness/results/<site>/gt-laser/` when that directory exists. `FIXTURE_SOURCE` defaults to the store `internal/` path. Set it to a private directory so a rebuild does not write the shared store.

<!-- gt-diff -->
eth3d-facade: differ=none (runtime_s ignored); kept committed scene.json; copied PLYs to .fixtures/gt/eth3d-facade/gt-laser
eth3d-electro: differ=none (runtime_s ignored); kept committed scene.json; copied PLYs to .fixtures/gt/eth3d-electro/gt-laser
eth3d-delivery: differ=none (runtime_s ignored); kept committed scene.json; copied PLYs to .fixtures/gt/eth3d-delivery/gt-laser

Rebuilt on this VM from the ETH3D archives (2026-09-27) with `make gt` (`build_gt` / `build_gt_c2`). `walls`, `chain`, `objects`, and `coverage.bands` match the committed scenes (`provenance.runtime_s` ignored). Electro and delivery `coverage.sensitivity` now come from that rebuild, the same `ground_sensitivity` path the facade scene already used: `rule_id` `angle75`, `angle80`, and `footprint3in` (`max_incidence_deg` 88, `max_footprint_m` 0.0762). The angle75 and angle80 bands are identical to the previous blocks. `footprint3in` is new (electro 17 ground bands, delivery 28). Facade sensitivity matched the rebuild band for band, including its existing `footprint3in` block.

The two colmap scenes (`colmap-nonlidar`, `colmap-oracle`) predated `rule_id`. `ground_sensitivity` on their committed grids and the store `points.ply` returns empty bands for all three rules, which is what those scenes now record, including an empty `footprint3in` entry.

PLY bytes: this VM's `make gt` reproduced the historical laser sha256s (`facade` `0a45bf5e444ef22f78feb97c1f8c7c5daff01937e32fc059835cb937a917a681`, `electro` `c04fd1cab9f5f1c2875858a3b32fe865f65e870bbe502a5364fa9e496c21c2c5`; delivery already matched). The store files from the earlier Mac rebuild differ by at most about 6e-8 m at the same vertex counts. Scene asset hashes were updated to those store files so `scene.json`, `manifest.json`, and the store bytes agree.

| Asset | sha256 now recorded in the scene (also the manifest and the store) |
|---|---|
| eth3d-facade `laser.ply` | `e23eb7d1f7a8338a46b499047edb4ef31d4f4cf5df9ea50ee95767cdced9ad3d` |
| eth3d-electro `laser.ply` | `e49bd68fe5c6f80c78712a9906b249141c3fb3760d64b301290f6aa319d8c125` |
| eth3d-delivery `laser.ply` | `54a08221ee1f70d2dc92b5e9a17207f3f6ca6b9ea0196f3163248b537b7ad642` (unchanged) |
| colmap-oracle `points.ply` | `7fab86a5f65302dd701d7658ebdebafcb07e621f922b35a82e5907f647ca7c76` |
| colmap-nonlidar `points.ply` | `35ebe838ed3ec16582040b2ed5a756025fa901ea10859d288afd5dfd93af333c` |

Occluder sha256s were already equal across the scene, the manifest, and the store. Reference verdicts and reference scores were not regenerated: walls, chain, objects, and primary bands are unchanged.

The brief's facade self-check said `n=55`. That number is `status_counts.scored`. `key_distances.overall.n` is 53 in the committed `reference-scores/eth3d-facade/geomscore-self.json` (two `wall.length` rows are `extent_limited` and stay out of `overall.n`). `make validate-full` compares `key_distances` to that file and requires `median_in`, `p90_in` and `scale_error_pct` of 0.

## Last gate table (pasted)

| Check | Expected | Observed |
|---|---|---|
| `make fixtures ONLY=small` | committed set verifies, exit 0 | PASS, 105 items, exit 0 |
| `make validate` | 3 GT scenes, 2 recon scenes, 11 verdicts, 29 labels, 36 packets | PASS, scenes=5, verdicts=11, labels=33, packets=36, exit 0 |
| `check-packets --files` on `mini-arkit/packet.json` | 0 errors | PASS, `ok` |
| facade `score-geometry` self-check | key distances equal the committed reference; median, p90 and scale 0 | overall n=53 (brief said 55; scored count is 55), median_in=0, p90_in=0, scale_error_pct=0 |

The labels count is 33 because `internal/criteria/labels-v0/` has 8 sites × A/B/C/S (32) plus `eth3d-facade.demo.labels.json`. The brief's 29 was 7 sites plus the demo. All 33 validate. No file was dropped.

## Known limits

WS-13's LaMAR rows and the three `unmirrored:` YouTube tars stay in `manifest.json`. `make fixtures` does not download those URLs (`scripts/lamar/fetch_lamar.sh` does) and does not fail when the unmirrored tars are absent. `lamar:lin-segments` is fetched from `breadth/a6-iphone-quality/results/segments.json` under the store `internal/` root.

Laser and occluder PLYs, recon `points.ply`, and the nine packet tars are fetch-only. GT PLYs live in the store under `internal/eval-harness/results/<site>/gt-laser/`, and the two colmap `points.ply` files live beside their scenes. Facade and electro `laser.ply`, and both colmap `points.ply` files, use the store rebuild's sha256 in the scene and in `manifest.json` (see the gt-diff note). Occluder hashes, and delivery `laser.ply`, match the same way. A later `make gt` can rewrite laser bytes; point the store copy at a private `FIXTURE_SOURCE` unless you intend to publish that rebuild.

`mini-arkit` is about 25 KB, under the brief's ≈ 60 KB estimate, because the JPEGs are solid 64×48 colour and the streams are two-row gzip CSVs.

Existing snapshot directories `packet/`, `recon/`, `scene/`, `criteria/`, `eval/`, `data/`, `docs/`, `scripts/` are still in the tree. This PR does not delete them.

`yt-12Ks1idWj40` is in the manifest as a fetched tar plus a committed `packet.photo-only.json`. It stays out of placement scoring and `e2e_packets`.
