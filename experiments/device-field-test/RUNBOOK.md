# Device test runbook: House Scan on a real iPhone

The Simulator can't run ARKit, so the product description on `t3/verification` marks everything that needs
the live camera as unverified. This runbook collects those items in the order a person meets them while
scanning. Every test has an ID, so a screen recording with spoken IDs can be matched back to the claim.

Sources, at `t3/verification`
[`63350b1`](https://github.com/SamGu-NRX/house-scanning-master/tree/63350b12495e7c69d18e6a4391e053a70f432dbd/verification/product):
the "Open questions and verification" section of each screen document, and the
[bug triage](https://github.com/SamGu-NRX/house-scanning-master/blob/63350b12495e7c69d18e6a4391e053a70f432dbd/verification/product/bug-triage.md)
for the `B-` numbers. "Known bug" means the triage already predicts the failure. The run confirms it on a device.

## Before the run

- Borrow a tape measure, painter's tape and a marker, plus a second phone for notes and for the call test (W-10).
- Set up the phone:
  - Add Screen Recording to Control Center, and long-press it to turn the microphone on. Haptics don't
    show in a recording, so say "haptic" or "no haptic" and the test ID out loud.
  - Charge the phone, free a few GB and turn brightness up.
  - Open `https://house-scanning-server.vercel.app/health` in Safari on the site's Wi-Fi.
- Check the TestFlight build: note its number, and check onboarding shows the "About 2 min" pill.
- Tape and write down the ground truth before anyone scans. [`setup-plan.html`](setup-plan.html) shows
  the readings used for the mock wall. At minimum, measure along the wall from the meter's centre line to:
  - both corners;
  - each opening's edges;
  - any gas meter;
  - any existing battery.

  Also measure the sill and eave heights and the depth of open ground in front of the wall.
- Keep the recording, the exported scan and the readings in `private/` or `data/`. They show a real
  site. Commit only derived numbers.

## Run 1: full flow, clean wall

O-3 Skip, F-3 a premature tap and F-2 a successful tap can leave their screens. Run each in a separate attempt, then restart before continuing the full flow.

| ID | Screen | Do | Expect |
|---|---|---|---|
| O-2 | Onboarding | VoiceOver on, swipe the pages | Unknown: is the page number announced? |
| O-3 | Onboarding | Reduce Motion on, tap Next and Skip | Known: Skip still slides |
| O-1 | Onboarding | Page through with Next, tap Allow camera, then Allow | The iOS camera prompt appears. Note when |
| F-3 | Find meter | Tap before the wall is detected | Question: does it pin at the wrong depth? |
| F-4 | Find meter | Point at the sky until relocalizing | Question: "Point at the meter like this." shows no picture |
| F-2 | Find meter | Tap the meter the moment the camera opens, then again | "Step a little closer to the wall"; known: no haptic on the second refusal |
| F-1 | Find meter | Pan across the wall and ground for 5 s, tap the meter | A ring blooms, a success haptic, then the close-up |
| C-2 | Close-up | Put the meter at the circle's edge | The drawn circle matches the captured area (device-only check) |
| C-1 | Close-up | Hold the meter in the circle | The ring fills in about 0.6 s, a light haptic, the walk begins 1.2 s later |
| W-1 | Walk | Follow each instruction | "Walk slowly to your left", "Tilt down…", "Take a step back". Is the fog readable? |
| W-2 | Walk | Watch the photo counter | It flashes green with no haptic |
| W-3 | Walk | Mark something, pick a kind, tap | A firm haptic on accept |
| W-4 | Walk | Tap a mark on the ceiling or sky twice | A warning haptic on the first refusal only |
| W-5 | Walk | Mark an AC unit | Known B-16: "Tap the ac unit" |
| W-7 | Walk | At about 20 ft: "Is this the … end?", then Wall ends here | Does the end land at the screen centre rather than the ring? Then the end question appears |
| W-8 | Walk | A wall that ends before 20 ft | Known B-06: only "Can't get there" is offered |
| W-9 | Walk | Move fast, aim at a blank surface, try a dark area | Tracking coaching appears |
| M-1 | Feature list | Read each mark and its distance aloud | Compare with the tape |
| G-1/G-2 | Gap request | Complete it, or tap I can't get there | A success haptic and the upload about 1.2 s later, or an immediate upload |
| U-1 | Upload | Time it | "About a minute" has never been measured |
| U-2 | Upload | Watch the steps | Known: stuck on "Sending photos, 99%", then "Checking your wall" is skipped |
| R-1 | Result | Read the headline and every "Measured … The rule is …" line aloud | A success haptic |
| R-2 | Result | Drag the 3D model vertically | Unknown: does it rotate the model or scroll the page? |
| R-3 | AR view | Walk 3 steps left and right | Does the battery sit on the real wall and stay put? |
| R-4 | AR view | Someone walks in front of the spot | Known: the drawing covers them |
| R-5 | AR view | Cover the lens for 3 s | Does the drawing hide while relocalizing? |

**Before any "Can't get there" on the walk**, stand at the meter and tilt down until the coverage strip next
to the meter is fully covered. Then check the strip is covered without a gap from the meter to where you
stand. Run 1 shows why: the tap puts the wall's end at the edge of the unbroken covered stretch, and if
that stretch is empty, the end lands at the meter. See [README.md](README.md#result-run-1).

How to fill it:
- Stand about 7 ft out from the meter and tilt down until the ground out to 4 ft from the wall is in view.
- Wait for the photo counter to flash, take a 2 ft step sideways, and wait for another flash. Do the same
  with the wall up to 6½ ft.

Each spot must be seen from two positions at least 0.25 m apart. Only kept photos count, and a photo is
kept after about 0.5 m of movement or 15° of turn.

## Run 2: the same wall with clutter

Put an obstacle in front of the meter or panel and repeat run 1. Add these:

| ID | Do | Expect |
|---|---|---|
| C-4 | On the close-up, shake gently so frames alternate sharp and blurry, for 30 s | Known B-04: the way out may never appear |
| C-3 | Then point away for 4 s or more, twice | "Can't get a clear shot" after the first failed try; the close-up is skipped after the second |
| W-13 | Walk past the obstacle, look from the side, then tap Can't see past it | "Something is in front of the wall here"; the cells behind it turn hatched. Needs a build with LiDAR occlusion (after 1.1) and a Pro iPhone |
| M-2 | Tap Add something on the feature list | Known B-09: nothing visible happens |

## Run 3: interruptions (separate short recordings)

Group the tests into short recordings: W-12 then M-3; W-10 then W-11; U-4 then U-3; O-4 alone. A call or a
lock can end a screen recording, and this way it takes only its own tests with it. O-4 deletes the app and
every scan on it, so share each completed scan before O-4 and confirm the exported file exists.

| ID | Do | Expect |
|---|---|---|
| W-10 | Take a phone call during the walk, then return | Unknown: does it recover? Does the recording survive? |
| W-11 | Start a mark, tap once, cover the lens for more than 20 s | Back to Find the meter with no explanation; known: the half-done mark survives |
| W-12 | Tap Can't get there twice at the start of the walk | Known: "That's the whole wall" with nothing walked |
| M-3 | On the feature list, point away for more than 20 s | Known: the list and every mark vanish with no warning |
| U-3 | Lock the phone during the upload | Unknown |
| U-4 | Airplane mode before the upload, then off, then Try again | "You're offline", then recovery; watch for raw error text (B-02) |
| O-4 | Last: delete, reinstall, tap Don't Allow at the camera prompt | Known B-03: stuck on Find the meter with no Settings button |

## After the run

- Relaunch preserves the retained completed scans.

- Save the scan's `scene.json` and replay its coverage:

  ```sh
  uv run coverage_replay.py path/to/scene.json
  ```

- Put the claims each recording settled into the table in [README.md](README.md#result-run-1), with the
  build number.
