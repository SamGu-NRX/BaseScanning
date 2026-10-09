# BaseScanning: House Scanning App for Base Core

Walk along the wall by your electric meter with an iPhone, and find out whether a home battery fits there and where it would go.

[Live site](https://house-scanning.vercel.app/) · [How it works](docs/how-it-works.html) · [Demo API](https://house-scanning-server.vercel.app/health)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/pipeline-dark.png">
  <img alt="Five steps from left to right: walk and mark, the capture packet, the placement rules, a spot or one more view, and the result in AR. A dashed loop runs from the fourth step back to the first: needs a view? The app asks for it." src="docs/readme/pipeline-light.png" width="100%">
</picture>

When a potential customer wants to determine whether a Base Power battery can be placed on their property, they have to send photos of their power meter and the wall it's on. Then, someone at Base has to decide where the battery goes using the photos the homeowner sends in.

That's a pretty rough way to do it. Measuring distances within a photo is challenging, and a photo is unable to illustrate what sits outside its frame. This could mean that another check would be needed -- a hassle for both Base and the interested homeowner.

We want to change that: the assessment should be just one walk. The homeowner opens our app and scans the outside wall around their meter. The app keeps guiding them, and asks for another angle whenever it needs one, until it has seen everything the placement rules care about. Then it sends what it captured to our server. The server builds a 3D model of the wall, checks every rule against it and sends back an answer. If the battery fits, the app shows it standing on the real wall through the phone's camera. Augmented Reality (AR) lets the homeowner see the spot before anyone installs anything.

Four of us built this for Base Power at a hackathon that started on September 25, 2026. [Who built this](#who-built-this) says who worked on what.

## Try it out

The quickest way to see it work is the demo server, which is already running. Ask it how it's doing:

```bash
curl -s https://house-scanning-server.vercel.app/health
```

It replies with `"status":"ok"` and a note about which rules it's using. The demo server knows only the public rules, never Base's own.

To work on the code, you'll need [uv](https://docs.astral.sh/uv/), Node 24 with pnpm, and Xcode 26 or newer. Clone the repository with its submodules and run the tests:

```bash
git clone --recurse-submodules https://github.com/SamGu-NRX/BaseScanning.git
cd BaseScanning
make check
```

`make check` runs the same server, web and iOS test suites that CI runs. Xcode runs only on a Mac, so on Linux or Windows, run `make server web` to skip the iOS suite.

## How the pieces fit together

Here's the whole trip, from the homeowner's phone to our server and back again. Each band is one part of the system, and each box names the framework and the call that does the work. The yellow shapes are the files that pass between parts. Dashed lines are optional, so the app works without LiDAR, and the reconstruction worker runs only when someone sends it the photos.

<p align="center">
  <img alt="Architecture in four swimlanes. On the iPhone, ARKit anchors the meter, Vision reads its number, Metal draws the coverage fog and RealityKit raycasts each tap, and the capture becomes scene.json. LiDAR depth is optional. URLSession posts scene.json to the placement server at POST /v1/placements. Keyframes can go to the optional reconstruction worker, where MoGe-2 depth scaled with OpenCV SIFT, or LiDAR depth, fuses into a NumPy TSDF and becomes a rebuilt scene.json for the same endpoint. The shapely solver reads rules.yaml and returns result.json. Back on the iPhone, SwiftUI shows the checks, RealityKit pins the spot to an AnchorEntity, and missing_evidence sends the homeowner back to walk the wall." src="docs/readme/architecture.svg" width="100%">
</p>

Here's what we didn't compromise on: while the ML models handle the fuzzy parts -- turning photos into a 3D wall, recognizing a gas meter -- they *never* make the call as to whether a battery fits. That decision comes from a deterministic, criteria-matching evaluation that holds each measurement against the bounds of its rule. If a measurement is within bounds, great: that check is a PASS. If it's out of bounds, the check is a FAIL. If the margin of error makes it too close to call, the check comes back UNSURE.

The criteria (e.g. the distance the battery must be from the gas meter) exist in a separate rules file. Changing a rule has no effect on the creation of a model, only its evaluation.

Below, one wall makes the whole trip, from photos to a checked spot. The models build the wall and name what's on it. The last step, the rule checks, is that deterministic evaluation.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/rebuild-dark.webp">
  <img alt="An animated drawing of one wall in six steps. Photos arrive, each with the phone's position. Points are matched between them. A ruler marks feet out from the meter. Dots fill in the wall and the ground, while the left side nobody filmed stays hatched and reads Not seen. The window, meter, AC unit and gravel get labels. Last, a battery appears 12 ft right of the meter with its cable, beside a card where wall behind it, ground under it, open space in front and a 13.6 ft cable run all pass." src="docs/readme/rebuild-light.webp" width="100%">
</picture>

Each part has its own folder:

| Part | Built with | Where |
| --- | --- | --- |
| iPhone app | Swift 6, SwiftUI, ARKit, RealityKit, Vision, Metal, XcodeGen | `ios/` |
| Rules engine and API | Python 3.12, FastAPI, shapely, jsonschema, uv, pytest, deployed on Vercel | `server/` |
| 3D reconstruction | Python 3.12, MoGe-2 on PyTorch, OpenCV, NumPy, SciPy, scikit-image | `recon/` |
| Reviewer view | TypeScript, Vite, Vitest, Biome | `web/` |
| Landing page | Static site on Vercel | `sites/landing` |

Here's where each piece runs. The phone uploads the capture to Google Cloud Storage and posts it to a FastAPI app on Modal. A coordinator there fans the work out to three workers: reconstruction, object detection and equipment reads. Their results meet in the scene builder, and the criteria engine checks the placed objects against the rules. The result composer then answers both the iOS app and the reviewer page. Model weights live on a Modal Volume, and each run's state lives in a Modal Dict. Dashed lines are optional or supporting paths, and the calls to OpenAI or xAI happen only when someone opts in.

<p align="center">
  <img alt="Infrastructure diagram. On iOS, SwiftUI with ARKit and LiDAR feeds CaptureRecorder, and URLSession uploads captures to Google Cloud Storage, reached through WIF and IAM, and posts to a FastAPI app on Modal. FastAPI hands the run to a Coordinator, which keeps run state in a Modal Dict and fans out to three workers. Reconstruction runs COLMAP with PyCOLMAP and SIFT, sets metric scale, builds depth with a LiDAR TSDF or π³, MoGe-2 and optionally DA3, and computes coverage. Object detection runs OpenCLIP or SigLIP2, then OWLv2 or Grounding DINO. Equipment reads runs ZXing-C++ in a reads worker, which also feeds metric scale. A Modal Volume holds model weights for reconstruction and detection. The scene builder turns the outputs into objects in 3D, which the criteria engine checks. The result composer sends the answer to the iOS result view and a reviewer HTML page. OpenAI or xAI external APIs are opt-in." src="docs/readme/infrastructure.png" width="560">
</p>

## Unseen? Then Unsure

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/unseen-dark.webp">
  <img alt="A wall with the left side hazed over. Three checks read Not seen. The haze sweeps away, the checks turn Unsure, then settle: wall and ground pass, clear space fails, and the spot reads Not here." src="docs/readme/unseen-light.webp" width="100%">
</picture>

This is the rule we care about most. Imagine that the phone never saw one stretch of wall. It might be bare, sure, but there very well could have been a gas meter on it. The server simply has no way to tell.

In this scenario, the server would mark that stretch of wall as unknown. It never assumes the best, and no battery placements depending on that stretch being empty would be considered valid.

You can watch that happen above. The homeowner walked to the right and never pointed the phone left, so the left side stays hazed over and the spot nearest it reads "Not seen yet". Once the view sweeps across, the server can finally judge that spot, and it turns out there isn't enough open space in front of it. The spot is resolved: unfortunately, there's no space for a battery.

Measurements get the same caution, because none of them is exact. The phone keeps track of where it is by adding up its own movements, a bit like finding your way by counting steps, so small errors pile up the farther you walk from the meter. That's why every measurement comes with a margin of error.

Take the 3 ft rule for gas meters. If the server measures 4.5 ft, give or take 0.8 ft, the battery clears the rule by more than the error, and the check passes. At 2 ft, give or take 0.8 ft, it falls short by more than the error, and the check fails. At 3.4 ft, give or take 0.8 ft, it could go either way. That check comes back UNSURE, and a person or a better view has to settle it. The numbers in the animation are made up to show the idea.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/margin-dark.png">
  <img alt="Three measurements against the 3 ft gas meter rule, on one scale in feet. 4.5 ft, give or take 0.8 ft, runs from 3.7 to 5.3 ft, all clear of the rule, and passes. 2 ft, give or take 0.8 ft, runs from 1.2 to 2.8 ft, all short of it, and fails. 3.4 ft, give or take 0.8 ft, runs from 2.6 to 4.2 ft, crosses the 3 ft line, and comes back unsure." src="docs/readme/margin-light.png" width="100%">
</picture>

## What the server checks

The battery measures 31 × 22 × 39.5 in, a bit bigger than a dishwasher. It stands on the ground, flush against the wall, within cable reach of the meter. To find it a spot, the server slides the battery's outline along every stretch of wall the phone saw, 2 in at a time, and runs all of these checks at every stop:

| Check | Rule | Source |
| --- | --- | --- |
| Wall behind it | The whole footprint backs onto one straight wall the camera saw | Base Core's size |
| Ground under it | A surface the rules allow | Demo choice |
| Meter's working space | Stays clear of the 30 × 36 in space in front of the meter | NEC 110.26 |
| Gas meter or pipe | At least 3 ft away | [Base's help page](https://help.basepowercompany.com/en/articles/10280705), Austin Energy §1.9, Texas Gas Service |
| AC units | At least 3 ft away | Base's help page |
| Doors and windows | At least 3 ft away | IRC R328.4 |
| Open space in front | At least 3 ft | Base's help page, for fences |
| Headroom | At least 6.5 ft | NEC 110.26, applied to the battery as a demo choice |
| Wall equipment | Nothing mounted on the wall above it | Demo choice |
| Cable run | At most 20 ft, and a person reviews anything past 15 ft | Base's help page. The 15 ft has no public source |
| Cable route | Can't cross a door, a garage or a gap in the wall | Demo choice |
| Driveway | At least 5 ft away | Placeholder, no public value |
| Pool | At least 10 ft away | Placeholder, no public value |

Each check comes back PASS, FAIL or UNSURE, along with what the server measured, how far off that measurement could be and a reason in plain English. NEC is the National Electrical Code and IRC is the International Residential Code, the two building codes behind several of these rules. The numbers themselves live in `server/rules.yaml`, each one next to its source, and [docs/04](docs/04-prior-art-and-codes.md) has the full citations.

The server then gives one of three answers for the whole scan. It says `pass` when a spot passes every check. It says `reject` only when every spot within cable reach fails and the app knows where the wall ends on both sides. Anything in between is `manual_review`, and it goes to a person.

### One request, start to finish

Here's what that looks like in practice. We took the example scene from `server/tests/fixtures/example-scene.json` and sent it to the demo server. The scene is made up. We wrote it by hand, with a gas meter, a window and an AC unit spread over two walls. The reply is real, though, trimmed to the parts worth reading. The demo server keeps taking PR #11's updates, so a reply you get today won't match these numbers.

The server stopped short of placing the battery itself. Its best spot was 9 ft 11 in left of the meter. From there, the AC unit around the corner measured 4 ft 1 in away, against a 3 ft rule. That sounds like a pass until you see the margin of error, which is give or take 3 ft 10 in. It's that wide because the AC unit sits far along the wall from the meter, where tracking error piles up the most. With an error that wide, the result is too close to call. So the answer is a manual review, plus a request to keep walking past the left end of the scan, because a spot within cable reach might be there.

<details>
<summary>What the phone sends: <code>scene.json</code>, shortened</summary>

```json
{
  "schema_version": "1.0",
  "meter": { "pos": [0.0, 5.0, 0.0], "wall_id": "side", "plus_minus_ft": 0.3 },
  "walls": [
    { "id": "back", "baseline": [[-14.0, -18.0], [-14.0, 0.0]], "height_ft": 9 },
    { "id": "side", "baseline": [[-14.0, 0.0], [22.0, 0.0]], "height_ft": 9 }
  ],
  "objects": [
    { "type": "gas_meter", "wall_id": "side", "span_ft": [-5.0, -4.0], "source": "tap" },
    { "type": "window", "wall_id": "side", "span_ft": [3.0, 6.0],
      "attrs": { "operable": true }, "source": "vlm", "conf": 0.86 },
    { "type": "ac", "wall_id": "back", "span_ft": [-20.0, -17.0], "source": "tap" }
  ],
  "coverage": {
    "ends": { "left": { "kind": "unexplored" }, "right": { "kind": "limit" } },
    "observed": [
      { "band": "wall", "span_ft": [-26.0, 22.0] },
      { "band": "ground", "span_ft": [-26.0, 26.0], "out_ft": 14.0 }
    ]
  },
  "keyframes": [
    { "id": "k1", "img": "k1.jpg", "intrinsics": [1450.0, 1450.0, 960.0, 720.0],
      "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2.0, 4.5, 9.0, 1] }
  ]
}
```

</details>

<details>
<summary>What the server sends back: <code>result.json</code>, shortened</summary>

```json
{
  "decision": "manual_review",
  "summary": "A person needs to check the best spot, 9 ft 11 in left of the meter: distance from ac units. Demo rules: public values and placeholders (pool 10 ft, drive 5 ft), not Base's.",
  "spot": { "outcome": "unsure", "wall_id": "side", "span_ft": [-11.24, -8.65], "route_length_ft": 9.65 },
  "checks": [
    {
      "id": "gas_clearance", "outcome": "pass",
      "measured_ft": 3.65, "plus_minus_ft": 0.6, "threshold_ft": 3.0, "comparison": "at_least",
      "reason": "Nearest gas meter or pipe is 3 ft 8 in (± 0 ft 7 in) away, clear of the 3 ft 0 in rule, and the area around the battery was seen."
    },
    {
      "id": "ac_clearance", "outcome": "unsure",
      "measured_ft": 4.08, "plus_minus_ft": 3.8, "threshold_ft": 3.0, "comparison": "at_least",
      "reason": "objects[3] ac is 4 ft 1 in (± 3 ft 10 in) from the battery against a 3 ft 0 in rule: too close to call."
    }
  ],
  "missing_evidence": [
    { "kind": "past_end", "side": "left",
      "message": "Keep walking past the left end of the scan (32 ft 0 in left of the meter): a spot within reach may be there." }
  ],
  "stats": { "candidates": 948, "pass": 0, "unsure": 423, "fail": 525, "elapsed_ms": 541.8 }
}
```

</details>

## Run the demo yourself

Everything below runs from `main`, which now holds the whole system: the app, the placement server, the reconstruction worker and the experiments. Four pull requests are still open: #7 (Measure Lab), #10 (the guided wall scan), #11 (the server's newest revision, which the hosted demo runs) and #21 (a new 3D coverage model).

**Ask the demo server for a placement.** The demo server runs the engine from PR #11 with public rules only, and every answer says so. The example scene ships with the repository:

```bash
curl -s https://house-scanning-server.vercel.app/v1/placements \
  -H 'Content-Type: application/json' \
  --data-binary @server/tests/fixtures/example-scene.json
```

For a drawing of the wall with the chosen spot, post the same scene to `/v1/placements/site-plan.svg` instead.

**Run the server on your own machine.** From the repository root, install the locked dependencies and start the API on port 8000:

```bash
cd server
uv sync --locked
uv run uvicorn api:app --host 0.0.0.0 --port 8000
```

Then send the same `curl` request to `http://localhost:8000` instead of the demo server.

**Run the app.** `main` has the whole guided walk: the meter close-up, the walk along the wall, the upload and the AR result. You don't need a phone to try it. Build `ios/HouseScan.xcodeproj` for the Simulator and pass the launch arguments `-replay <capture folder> -autopilot -serverURL https://house-scanning-server.vercel.app`. `-replay` plays a recorded capture in place of the camera, and `-autopilot` steps through every screen for you. Two made-up captures ship in `ios/HouseScanUITests/Fixtures/`: `synthetic-wall` and `synthetic-wall-lidar`.

To run it on a real iPhone, set up signing first:

```bash
cp ios/Config/Local.xcconfig.example ios/Config/Local.xcconfig
```

Fill in `DEVELOPMENT_TEAM` and `BUNDLE_ID_PREFIX` in that file, plug in the phone and run. You might be tempted to pick your team in Xcode's Signing & Capabilities pane instead. Don't. Xcode writes that choice into the project file, and CI fails on the difference.

### Environment variables, all optional

You don't need any API keys, and the server runs the public rules with nothing set at all. If you want to change how it behaves, put any of these in `server/.env`:

```bash
# server/.env  (git ignores .env files; load it with: uv run --env-file .env uvicorn api:app)

# "strict" sends every would-be pass or fail to manual review instead of deciding.
# HOUSESCAN_POLICY=strict

# HOUSESCAN_PRIVATE_RULES=../private/rules.yaml
# HOUSESCAN_PRIVATE_RULES_B64=<the same YAML, base64-encoded, for Vercel>

# Required once private rules load. Every request then needs it, except /health and CORS preflight (OPTIONS).
# HOUSESCAN_API_KEY=<a long random string you choose>
```

Two more belong to other parts of the project. The reconstruction worker in `recon/` looks for public datasets and cached models in `HOUSE_SCANNING_DATA`, which defaults to `~/house-scanning-data`, and posts its results to the server named in `HOUSESCAN_SERVER`. TestFlight uploads use repository secrets, and [CONTRIBUTING.md](CONTRIBUTING.md) describes them.

## Where our data came from

Here's every dataset we used, what we used it for and whether it's in the repository.

| Data | What we used it for | Source | In git? |
| --- | --- | --- | --- |
| [ADVIO](https://github.com/AaltoVision/ADVIO) | Tracking drift on an iPhone 6s | Public, CC BY-NC 4.0 | No |
| MARViN | Tracking drift on an iPhone 14 Pro Max | Public, no license stated | No |
| [ETH3D](https://www.eth3d.net) | Wall error against a laser scan | Public, CC BY-NC-SA 4.0 | No |
| Meter photos | Reading the meter number (PR #16) | Photos of real meters taken for this test | No, `data/` |
| Field runs | The app's first run on a phone (PR #23) | Our TestFlight build on a real wall | No, `captures/` |
| Rule values | The rules engine | Public pages and building codes, each cited in [docs/04](docs/04-prior-art-and-codes.md) | Yes |
| Test fixtures | Server and packet tests | Synthetic, written by hand | Yes |
| Drawings and animations | This README, the site, the walkthrough | Illustrations with example values, not a real house | Yes |

We use the public datasets only to measure accuracy, and we never redistribute them. ADVIO and ETH3D are licensed for noncommercial use, so if you want to rely on these results for commercial work, ask their authors for permission first.

## How accurate it is so far

Most of these numbers come from public datasets where someone already measured the real answer with a laser scanner or survey equipment, so we could grade our results against it. Only the meter photos and the first phone run are our own.

A few terms first, in case they're new to you. Tracking drift is how far the phone's sense of its own position wanders as you walk. Learned depth is a neural network guessing distances from a single photo. Rescaling it with the phone's poses means correcting those guesses using where the phone was, and which way it faced, for each photo. And p90 means 9 out of 10 measurements were off by that much or less. We report p90 rather than an average, because an average hides the bad misses, and those are the ones that put a battery in the wrong place.

| What | Result | Source |
| --- | --- | --- |
| Tracking drift, recent iPhone | 8.6, 13.4 and 18.5 in (p90) after 10, 20 and 30 ft, inside the server's allowance of 19.2, 38.4 and 57.6 in | MARViN, iPhone 14 Pro Max, `experiments/evals/results/modern_arkit.md` |
| Tracking drift, older iPhone | Two to three times over that allowance | ADVIO, iPhone 6s, `experiments/evals/results/advio_drift.md` |
| Learned depth on its own | Scale 4 to 12% off, which puts walls about 20 in out (p90) | ETH3D, `experiments/evals/results/eth3d_recon.md` |
| Learned depth, rescaled with the phone's poses | Walls within about 5 in (p90), or 2.8 in with exact poses. Edges stay at 8 in or worse | ETH3D, `experiments/evals/results/pose_priors.md` |
| Reconstruction worker | Walls within 1.8 in (p90) with a laser scan standing in for LiDAR, and 1.5 in from photos only | ETH3D, `recon/results/eth3d_electro.md` |
| Reading the meter number | Read in full on 71 of 73 photos, but the right line on only 21 of 75. A list of three candidates held it on 27 of 34 held-out photos | Photos of real meters, `experiments/meter-closeup/results/clean.md` and `locate.md` |
| First run on our phone | Both wall ends landed at the meter, so the server placed no spot. Two of five features came within 4 in of the tape | TestFlight build, `experiments/device-field-test/README.md` |

## What doesn't work yet

Here's what we know is either missing or broken:

- **Four pull requests are still open.** #10 and #21 carry app work (the guided wall scan, and a new 3D coverage model), #7 carries Measure Lab, and #11 is the server's newest revision, which the hosted demo runs. Reconstruction, the packet notes, the evals, meter reading and the first field test have all merged to `main`.
- **Our first real phone run placed nothing.** The app put both ends of the wall right at the meter, which left the server a wall with no length to search (PR #23).
- **We haven't settled on how to build the 3D model.** A depth model on its own puts walls about 20 in off. Correcting its scale with the phone's poses brings that down to about 5 in, but edges stay 8 in off or worse, and the clearance rules measure from edges. With a laser scan standing in for LiDAR, walls land within 1.8 in.
- **Our best tracking result came from a phone with LiDAR.** An iPhone 14 Pro Max stayed inside the error allowance, but it has LiDAR, and most homeowners' phones don't. An older iPhone 6s ran two to three times over.
- **Nobody has taken on hidden walls yet.** A bush or a trash can in front of the wall can hide what's behind it, and on phones without LiDAR, nothing checks for that.
- **Two rules use placeholder numbers.** We couldn't find a public value for how far a battery should sit from a pool or a driveway, so for now they're 10 ft and 5 ft.
- **Reading the meter number is only half solved.** The phone reads the text fine, but a meter's nameplate carries several numbers, and the phone picked the right one on only 21 of 75 photos. For now the app shows three candidates and lets the homeowner tap the right one.
- **We don't look at the electrical panel.** An electrician still has to review it.

## What we'd do next

If we keep going, this is where we'd start:

1. Take a current iPhone without LiDAR to a real wall and run the field test in `experiments/evals/field/FIELD_SHEET.md`. The same trip checks our default error bars against a tape measure.
2. Pick a way to build the 3D model. We'd also like to try world models, which generate a whole 3D scene from photos or video. Nobody here has tested one yet.
3. Decide who owns the hidden-wall check. One idea is to show the homeowner the photo of the chosen spot and ask them.
4. Swap the placeholder values for Base's real ones on the private deployment.

## Who built this

| Name | Role | Contact |
| --- | --- | --- |
| Sam Gu | The iPhone app and the capture packet, built with AI agents | [@SamGu-NRX](https://github.com/SamGu-NRX) |
| Aiden Johnston | Field tests on a real iPhone, the app fixes they turn up, video and sample data | [@AidenJohnston](https://github.com/AidenJohnston) |
| Hunter Carver | The 3D model and the rule checks, built with AI agents | [@huntertcarver](https://github.com/huntertcarver) |
| Shrey Suri | This README, its diagrams and the writing skills the agents share | [@ShreySuri](https://github.com/ShreySuri) |

## Where everything lives

Everything in the table is on `main`. The four open pull requests are #7, #10, #11 and #21.

| Path | What it is | State |
| --- | --- | --- |
| `ios/` | The iPhone app | On `main`, the whole guided walk through the AR result. App work continues in open PRs #10 and #21 |
| `packet/` | The app side's notes on the server team's capture packet | On `main` |
| `server/` | The rules engine and placement API | On `main`. The hosted demo runs the newest revision, in open PR #11 |
| `recon/` | Turns photos and depth into a 3D model and a coverage map | On `main`, with benchmark results in `recon/results/` |
| `experiments/` | One folder per experiment | On `main`: accuracy evals, Measure Lab, scoring, meter reading, the first device field test, and more |
| `verification/` | Checks that the app and server do what the plan says, with a scoreboard | On `main` |
| `web/` | Browser app for capture experiments | A placeholder page and a units library; no reviewer view yet |
| `docs/` | The overview, the walkthrough, public rules and code citations, and the live-survey design | |
| `.agents/skills/` | Shared agent skills for writing, planning and review, linked from `.claude/skills/` | |
| `sites/landing` | The landing page, a submodule | Change it in its own repository |

If you want to go deeper, start with [the walkthrough](docs/how-it-works.html). It takes about ten minutes and has plenty of pictures. After that, [docs/00-overview.md](docs/00-overview.md) covers the plan, the decisions we made and the evidence behind them. If you're going to change anything, read [AGENTS.md](AGENTS.md) for the rules of the repository and [CONTRIBUTING.md](CONTRIBUTING.md) for branches, CI and TestFlight.

The server team's research handoff, with its findings, tested prototypes and the field work still to do, is in [docs/06-research-handoff.md](docs/06-research-handoff.md). TestFlight builds of the app and Measure Lab start by hand from the Actions tab, as [CONTRIBUTING.md](CONTRIBUTING.md) describes.

The repository is public. Most pictures in this README come from the [live site](https://house-scanning.vercel.app/). We drew the rebuild animation and the gas-meter figure for this README, in the same style. Materials Base gave the team stay in the git-ignored `private/` folder, and photos of real homes never enter git.
