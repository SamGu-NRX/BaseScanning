# Real-house and packet registry

Status read on 2026-09-27. The store packet index, draft labels, and fold-in docs were updated. This file is the repo copy of that status.

## Built packets

`internal/data/packets/index.json` lists 12 houses and `not_built` is empty. The three new rows are local tars and are not mirrored into `archives/`.

| house_id | in the index |
|---|---|
| eth3d-electro | built |
| eth3d-facade | built (archive is two parts) |
| tt-barn | built |
| yt-0gotY_syWWU | built |
| yt-12Ks1idWj40 | built |
| yt-4h8kyjnPo9s | built |
| yt-5-M-7f35Tyg | built |
| yt-LCPBf86oloE | built |
| yt-Qm4b1cw8pV4 | built |
| yt-Y0DSQekoV_g | indexed; local tar, not mirrored |
| yt-KSXy6LFwQU4 | indexed; local tar, not mirrored |
| yt-olz1uwI4vM8 | indexed; local tar, not mirrored |

House directories also exist for `yt-KSXy6LFwQU4`, `yt-Y0DSQekoV_g`, and `yt-olz1uwI4vM8`. The plan's id `yt-KSXy4LFwQU4` is a typo for `yt-KSXy6LFwQU4`. Those three ids are now in the houses array.

`internal/criteria/labels-v0/` has the previous 32 files plus the 28 drafts from `data/fixtures/labels/` (5 YouTube sites and `real-001` / `real-002`, each A/B/C/S). YouTube `site_decision` is `manual_review` and every criterion is `unknown`. Real-house files are templates: `site_decision: unknown`, site scope only, no `start_range` row. A `start_range` label needs `wall_id` and `start_s_m`, and those were not measured. They are marked draft for Hunter. `docs/house-labels.md` was not moved to v0.6.

`yt-Y0DSQekoV_g` was built with packetgen from the store frames. Four variants validated. 92 keyframes, 6 views dropped. The local tar is not in git. Its sha256 is `95fcae5849c40bf008dceb7a680a3bd57c85c8bb79cd90b9b4b5160afd4dc85a` and its size is 26368000 bytes. The fixture row id is `yt-Y0DSQekoV_g`, `kind: packet_tar`, `cache_path: .fixtures/packets/yt-Y0DSQekoV_g.tar`, `committed: false`.

`yt-KSXy6LFwQU4` built four variants. 176 keyframes, none dropped. Local tar sha256 `8fabeb1639f937be058786d357f6e6b13cc6e90f5c293cc5116eac678a12d7d4`, 57221120 bytes. Same `packet_tar` shape, `committed: false`.

Packet JSON is not in git. Location keys were stripped in the local tar, and the pretty-printed JSON still has 8-digit runs inside exposure and pose numbers. Frames are not committed.

`yt-olz1uwI4vM8` built four variants. 126 keyframes, none dropped. The largest model registered 126 images. That is above the 30 percent line, so no photo-only fallback was recorded. Packetgen has no fisheye switch. Local tar sha256 `378b68fed0f5cc4d5cf1a4b59041575e8a526b01cac824bd4f7f78811cffd8ef`, 54662144 bytes, `kind: packet_tar`, `committed: false`. The tar omits the GPS stream, `house.json`, and the truth folder. The dataset title was replaced with the house id before hashing.

## Real houses

`real-001` and `real-002` wait for capture day. No address, owner, face, or meter number is stored. Consent text is `scripts/real_house/consent-template.md`. The shot list is `scripts/real_house/field_sheet.md`.

## LaMAR fixtures

`data/fixtures/manifest.json` has eight `kind: lamar` rows (`cache_path` under `.fixtures/lamar/`), `committed: false`, `sha256` and `bytes` null until `scripts/lamar/fetch_lamar.sh ~/lamar` runs. The download is about 6.1 GB and is not part of this pull request. A6's helpers read `~/lamar`. YouTube rows use `kind: packet_tar` and the house id, matching the fixture manifest shape in the WS-00 brief.

## Path deviations

| Brief path | Where it landed |
|---|---|
| `internal/build/fold-ins/*.md` | `docs/data/fold-ins/` and store `internal/build/fold-ins/` |
| `internal/build/real-house-consent-template.md` | `scripts/real_house/consent-template.md` |
| `docs/evidence-register.md` | `docs/evidence-register.md` (as specified) |
| Store packet index and `labels-v0/` drafts | written; other store paths in the brief were not |

GitHub issues for the fold-in owners were not opened. Titles are listed in each fold-in file.
