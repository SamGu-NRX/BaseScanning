# server

The placement server. It takes a scan of the wall around the electric meter and answers whether a Base Power battery fits, where, and why. The decision is plain code; every threshold lives in `rules.yaml` with its citation.

## Run

Needs [uv](https://docs.astral.sh/uv/) 0.11. From `server/`:

| Command | What it does |
| --- | --- |
| `uv sync --locked` | Install |
| `uv run uvicorn api:app --host 0.0.0.0 --port 8000` | Serve the API |
| `uv run python siteplan.py scene.json plan.svg` | Solve one scene and write its site plan |
| `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format .` | Test and lint (`make server` runs what CI runs). With `CI` set, as GitHub Actions sets it, the property tests run a fixed set of examples; locally they search at random |

Without the web layer: `loaded = load_rules()`, then `solve(parse_scene(raw, loaded.rules), loaded)` (from `rules`, `scene` and `solver`).

## Deploy

Two Vercel projects deploy this directory. Neither is connected to Git, so a merge doesn't redeploy them; only `server/` uploads (`.vercelignore`), so the repository's `private/` folder never reaches a deployment.

| Deployment | Rules | Access |
| --- | --- | --- |
| **https://house-scanning-server.vercel.app** (`house-scanning-server`) | public, under the demo policy | open |
| **https://house-scanning-server-private.vercel.app** (`house-scanning-server-private`) | public with the private rules merged over them | `Authorization: Bearer <key>` on every route but `/health` |

Redeploy the public one from this directory:

```sh
npx vercel@latest deploy --prod --scope sam-gus-projects-7a4b6082
```

The private one gets its rules from environment variables set in its Vercel project, never from an uploaded file: `HOUSESCAN_PRIVATE_RULES_B64` holds `private/rules.yaml` base64-encoded, and `HOUSESCAN_API_KEY` the key. While private rules are loaded, every route but `/health` answers 401 without the key and 503 if no key is set, and `/health` says only that private rules are loaded. The key is in `server/.env.private.local` (git-ignored, mode 600) on the machine that set it up. This directory is linked to the public project, so deploy the private one from a copy of it that is linked to `house-scanning-server-private`:

```sh
rsync -a --exclude .venv --exclude .vercel --exclude '.env*' --exclude tests ./ /tmp/private-deploy/
cd /tmp/private-deploy
npx vercel@latest link --yes --project house-scanning-server-private --scope sam-gus-projects-7a4b6082
npx vercel@latest deploy --prod --scope sam-gus-projects-7a4b6082
cd - && rm -rf /tmp/private-deploy   # the link leaves a Vercel token in .env.local there
```

Vercel caps a request body at 4.5 MB, so send bare `scene.json`: the solver doesn't read the images, and a zip must hold every JPEG its `scene.json` names or it is refused with 422 `missing_bundle_file`. `examples/` has scenes and `curl` commands for both deployments, and `make smoke URL=...` posts them all.

## API

| Endpoint | Body | Returns |
| --- | --- | --- |
| `GET /health` | | Which rules are loaded, and their hash |
| `POST /v1/placements` | a scene | The result, `schemas/result.schema.json` |
| `POST /v1/placements/site-plan.svg` | a scene | The site plan, `image/svg+xml` |
| `GET /v1/schemas/scene.json`, `GET /v1/schemas/result.json` | | The contract this server honours |

A scene is bare `scene.json` (`application/json`), a zip holding `scene.json` and the JPEGs it names (`application/zip`), or either one as the multipart field `bundle`. `/openapi.json` declares all three.

```sh
curl -s localhost:8000/v1/placements -H 'Content-Type: application/json' \
  --data-binary @tests/fixtures/example-scene.json | jq .decision
```

A refusal is `{"error": {"code", "message", "path"}}`, where `path` is the JSON pointer of the field at fault:

| Status | When |
| --- | --- |
| 400 | The body or zip can't be read, or a zip entry has an absolute or `..` path |
| 413 | Over `HOUSESCAN_MAX_UPLOAD_MB` (default 256), unzipped over `HOUSESCAN_MAX_UNZIPPED_MB` (default 512), or `scene.json` itself over `HOUSESCAN_MAX_SCENE_MB` (default 10) |
| 415 | Not JSON, zip or multipart |
| 422 | The scene breaks `schemas/scene.schema.json`, names an image the zip lacks, can't be placed (for example the meter is far from its wall), or needs more than 50,000 positions or 8 s to check (`scene_too_complex`; refused as soon as the pace of the first positions shows it) |

## Writing a scene

`schemas/scene.schema.json` is the contract; it only gains optional fields. Lengths are feet.

- Wall `baseline` points run **left to right as seen from outside**, and walls are listed in that order. That fixes each wall's outward side.
- `s` is feet along the walls from the meter, negative to the left. `span_ft` and coverage use it.
- `coverage` lists what the capture actually saw, and whether each end of the walk is a real `limit` or `unexplored`. Anything unseen makes the checks that depend on it UNSURE, so a scene without `coverage` can't pass.
- A limit end (a fence, a corner) doesn't clear the ground beyond it: a pool there still counts. Ground seen past a limit end, a `span_ft` beyond the chain's end, covers both sides of the wall's continued line, so pointing the camera past the end settles it, also where it lies within reach of an unexplored end at the other side. Past an unexplored end only walking on does.
- Consecutive walls meet when the space between one's end and the next one's start is within both walls' position errors (capped at `sweep.wall_join_ft`, 0.6; walls with zero declared error meet only where their ends coincide); `s` then continues from the first wall's end without that space. A wider space is a gap: a stretch with no wall that no battery backs onto and no cable crosses, and `s` counts its length. The ground behind a gap (a side passage) is yard like the ground in front. A ground view shows it only when the view's span lies within the gap, the camera pointed into the passage; such a view covers both sides of the gap's line out to its `out_ft`. A view that runs along the walls across a gap shows only the front of it. A scene with more than 200 straight wall segments, counted after nearly collinear taps merge, is refused, because the solve grows faster than their square.
- Give an object a plan `footprint` when it stands off the wall (a regulator, an AC unit), or clearances are measured to its stretch of wall line.
- Omit `plus_minus_ft` and AR-placed positions get `rules.yaml`'s default error for their source plus 0.16 ft per foot along the walls from the meter, from measured ARKit drift. Send your own when you know better.

## What settles each check

A check is settled when everything it depends on is observed and measured; then it passes or fails on the numbers. What follows is the coverage (`coverage.observed`) and the measurements each check needs, so a capture that supplies exactly this gets a decision on the first upload. Values in brackets are the public rules (`rules.yaml`); private rules may differ, and a request always names the exact span and depth.

Notation, all in feet: the battery stands at s from `s0` to `s1` (width W = 2.58, depth D = 1.83). `e` is the battery's position error: its wall's error at the battery's far edge from the meter (the wall's `plus_minus_ft`, or the default for its `source`: tap 0.3, the default when `source` is absent; mesh 0.5; plane 0.75, an untested estimate; plus 0.16 per foot along the walls from the meter), plus the meter's error against anything placed in plan (an object's `footprint`, a ground patch, a wall's end), or plus how far the battery can slide against anything placed along the walls in s (a span); a check that compares with both kinds takes the larger. The battery is placed by its offset from the meter, so it moves with the meter against anything placed in plan. Against a span it moves only when it stands round a corner from the meter: moving the meter or a corner between them then slides it by that move's error times the difference between the two walls' directions (0 on the meter's own wall, √2 at a right angle). A hazard with no `footprint` lies on its wall's line at its span, so against one on another wall a clearance also adds, for each wall between the two other than the battery's, that wall's error times one plus its turn against the battery's wall: the wall's line moves, and its corners slide what lies past them. `r` is a check's rule value. A ground band "over [a, b] out to d" means an observed `{"band": "ground", "span_ft": [a, b], "out_ft": d}` (or several that together cover it). A wall band "seen higher than h" means wall entries whose `out_ft`, the height up the face the view reached, exceeds h; an entry without `out_ft` counts as seen to headroom height (`headroom.min_ft`), inclusive: it settles a check needing up to that height, and no more. An explicit `out_ft` must be strictly higher than the height a check needs, so an explicit 6.5 does not settle a check needing 6.5 while an omitted one does. The asymmetry keeps every answer for scenes that omit wall heights unchanged, and the finite default stops an omitted view from settling a check that needs more (an 8 ft battery's back). Heights come from `rules.yaml` and are compared as given: nothing in the rules models a vertical error, so report a height you are sure of.

| Check | Reads | Coverage that settles it |
| --- | --- | --- |
| `wall_backing` | `walls`, and `height_ft` when given | the footprint clear of its straight segment's ends by e (within e: UNSURE), and wall band over [s0 − e, s1 + e], seen higher than the battery (3.29); each wall behind the battery that declares `height_ft` must be taller than the battery |
| `ground_surface` | `ground` patches | ground over [s0 − e, s1 + e] out to D + e, and a patch of an allowed type under the whole footprint |
| `meter_working_space` | `meter` | nothing to observe |
| `gas_clearance` (r = 3) | `objects` of type `gas_meter`, with `footprint` when it stands off the wall | ground over [s0 − r − e, s1 + r + e] out to D + r + e, and wall band over the same span, seen higher than headroom height (6.5) |
| `ac_clearance` (r = 3), `pool_clearance` (r = 10) | `objects` of type `ac`, `pool` | ground over [s0 − r − e, s1 + r + e] out to D + r + e |
| `battery_clearance` (r = 3), when the scene marks one or the rules set it above `gas_ft` | `objects` of type `battery`, with `footprint` (a battery already installed; issue #27) | ground over [s0 − r − e, s1 + r + e] out to D + r + e, and wall band over the same span, seen higher than headroom height (6.5). The cable detours over an existing battery on its route |
| `drive_clearance` (r = 5) | `ground` patches of type `drive` | as above |
| `opening_clearance` (r = 3) | `objects` of type `door`, `window`, `garage_door`; `attrs.operable` and `attrs.well` for windows | wall band over [s0 − r − e, s1 + r + e], seen higher than headroom height (6.5), or than `openings.exempt_bottom_above_ft` when the rules set a lower one; rules setting it above headroom height are refused at load |
| `wall_equipment_above` (r = 0) | `objects` of type `elec_box`, `vent` | wall band over [s0 − r − e, s1 + r + e], seen higher than headroom height (6.5) |
| `facing_gap` (r = 3, from the battery's front) | `facing` measurements | facing band over [s0 − e, s1 + e], every place the battery may sit. Where no `facing` entry covers it, the band's `out_ft` must exceed D + r (4.83): a walked path proves the space clear out to where the homeowner walked. No `out_ft` means the view reached whatever faces the wall, and it is in `facing` |
| `headroom` (r = 6.5) | `overheads` measurements | overhead band over [s0 − e, s1 + e]. Where no `overheads` entry covers it, the band's `out_ft` (height seen clear) must exceed r. No `out_ft` means seen clear all the way up, as from a tilt-up view of open sky |
| `route_path` | `walls`, openings on the route (the run starts at the meter, so its distance from the wall's line counts toward `route_length`, and every wall it runs along adds its error) | wall band from the meter to the battery's near edge, widened at each end by that end's error (the meter's, the wall's), seen higher than the cable's run (`route.height_ft`, 1.0) |
| `route_length` | `walls`, `meter` | nothing to observe |

Errors: a measured value passes only when it clears the rule by more than its error. Objects take the default error for their `source` (tape 0.05, tap 0.3, vlm 1.5, plus 0.16 per foot along the walls for tap and vlm) unless they carry `plus_minus_ft`, and walls likewise (tap 0.3, mesh 0.5, plane 0.75, each plus the same drift). Send a wall's `source` when its line comes from the mesh or detected planes rather than taps, so the error bars match how it was measured; `facing` and `overheads` entries default to the mesh error, 0.5. Every `out_ft` is taken as exact, so report the distance you are sure of (for a walked path, the distance from the wall less your position error). Ground that was seen but has no recorded surface may be a driveway, so within `drive_clearance`'s reach it leaves that check UNSURE as an unknown attribute until a `ground` patch records it, as it does for `ground_surface` under the battery. When a check at the best spot needs ground past an unexplored end, the answer asks to walk past that end, even an end too far for a spot past it to reach. The meter's working space is drawn in front of the wall under it, so its check adds that wall's error to the meter's and the battery's wall's. A battery overlapping it from another wall is measured by how deep the overlap is. The cable run's length is judged over the range it can truly take: moving the meter by d moves both the route's start and the battery, changing the run by d·(v ± u) less up to twice the meter's standoff (v: the difference between the battery's and the meter's wall directions, signed by the battery's side; u: from the wall to the meter), so it can lengthen by up to max(e·|v + u|, e·|v − u| − 2·standoff), √5·e round a right angle with the meter on its wall's line, and shorten only as far as the standoff allows.

Ends and corners:

- A corner the walk follows is the next wall in `walls`, starting at the corner point; the chain is then continuous and the corner raises nothing. Keep walking until the answer's `ends.<side>.beyond_reach` is true or the wall really ends.
- `unexplored` means the wall continues. Within cable reach it raises a `past_end` request when no spot passes, because a spot may be round it; nothing else settles it.
- `limit` means no usable wall past it. Ground past a limit end still counts for clearances (a pool behind a fence is still a pool): show it by pointing the camera past the end, reported as a ground span beyond the chain's end, which covers both sides of the wall's continued line.

Ground behind a scanned wall that is also on the house side of every scanned wall's line is the house and never counts as unseen; anything else in front of any wall is yard, so a wall running back behind another can't hide the yard in front of it. Seen ground closes gaps narrower than the 0.01 ft tolerance and no wider. A ground request's `out_ft` is the smallest depth, over its span, for which the unseen ground in question would be covered, computed with the same geometry the checks use. Supplying exactly what a request in `missing_evidence` names settles it: a `coverage.observed` entry with its `band` and `span_ft`, and an `out_ft` at least the request's `out_ft` (ground, facing, overhead and wall requests carry one).

## Reading a result

`decision` is `pass`, `manual_review` or `reject`. `spot` gives the battery's plan footprint and `meter_offset_ft`, its offset from the meter for AR; `route` is the cable run. Each entry in `checks` has an outcome, a reason, the measurement, its error and the rule it was held to:

| Check | Means |
| --- | --- |
| `wall_backing` | Flush on one straight, observed stretch of wall |
| `ground_surface` | Standing on an allowed surface |
| `meter_working_space` | Clear of the NEC 110.26 space in front of the meter |
| `gas_clearance`, `ac_clearance`, `pool_clearance`, `drive_clearance`, `opening_clearance` | Far enough from every part of each object, and the area in between was seen |
| `wall_equipment_above` | No box or vent on the wall above |
| `facing_gap`, `headroom` | Enough room in front and above |
| `route_path`, `route_length` | The cable runs along continuous wall, and isn't too long |

A check passes only when its margin exceeds its error and fails only when it misses by more than its error; anything else is UNSURE with an `unsure_cause`. `missing_evidence` lists the views that would settle an unseen area; showing exactly what a `band` request names settles it, and a `past_end` request asks to walk on. When a measurement only might lie in front of or over the battery (its end is within the wall's position error of the battery's edge), `measured_ft ± plus_minus_ft` spans the values the check could have, so the numbers give the same UNSURE. A reject needs every spot within cable reach to fail and both ends of the walk known. `objects_not_used` names marks the server didn't measure: objects with no `footprint` past an `unexplored` end, where the wall may turn, so their place is unknown. Among UNSURE spots, one whose best estimate is past a rule (for example overlapping the meter's working space) ranks after every spot whose estimates all clear, whatever its cable run.

## Rules

`rules.yaml` holds the public values. `placeholder: true` marks a value with no public source (the pool's 10 ft and the driveway's 5 ft among them). Its `demo` policy decides automatically anyway, so the team can test real passes and rejects, and every answer says so: `policy.notice` and the end of `summary` read "Demo rules: public values and placeholders (pool 10 ft, drive 5 ft), not Base's." `HOUSESCAN_POLICY=strict` selects the earlier behaviour instead: `auto_approve: false`, so every would-be pass or reject goes to manual review. At startup the server merges private rules over the public ones when there are any: `HOUSESCAN_PRIVATE_RULES_B64` (the YAML, base64-encoded), else `HOUSESCAN_PRIVATE_RULES` (a path), else `<repo root>/private/rules.yaml` if it exists. Setting both variables is an error. Private rules replace the policy and its demo notice; while any public placeholder is still in effect, `policy.notice` names the checks it decides (and any other placeholder settings), so a partial private file never reads as complete. Every value the private file sets must carry its own `source`, or the server refuses to start; answers show those citations only as "Private rules". Never commit Base's values. Base-derived tests live beside them in `private/tests/`: `uv run pytest ../private/tests -q`.
