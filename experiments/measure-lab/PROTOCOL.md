# Run the Measure Lab outdoor test

This is the runbook for the one-hour tape test that answers the question in the [README](README.md). It lists the pass criteria, how to install the app, what each tool does, the steps, and the sheet to fill in. [SESSION-FORMAT.md](SESSION-FORMAT.md) documents the files each session writes.

## Pass criteria

These criteria come from the [no-LiDAR research note](https://github.com/SamGu-NRX/house-scanning-master/blob/9737e3f0eefe90f2a12a190bf8750e7fed64413f/docs/research/t3-no-lidar-capture.md) and were set before any run. The method passes when all of them hold:

- Wall, opening and rigid-ground distances are within 4 in of the tape. The facing gap and overhead height are within 6 in.
- The 30 ft span is within 8 in. The return-to-reference gap is within 4 in.
- Every accepted measurement's interval contains the taped value. The interval is the app value ± the bound above. A measurement with `accepted: false` is an abstention. Record its error anyway, to show whether its warnings were needed.
- The hidden-contact, low-parallax, mismatched-tap and wrong-plane cases abstain with a refusal, a flag or a failed wall check.
- On both sides of a threshold, the decision rule never gives a false PASS. Use the public 3 ft fence clearance from Base's help page as `T`. The rule is PASS when `distance − bound > T`, FAIL when `distance + bound < T`, and UNSURE otherwise. Equality is UNSURE, as in the check rule in `docs/00-overview.md` ("Every check answers PASS, FAIL or UNSURE").
- The uncoached operator finishes one capture in 8 minutes, with at most one corrective prompt per measurement.

If a criterion fails, narrow the method's claim or send that quantity to review. Never widen the bounds to pass. One house can admit a method to the demo. It cannot prove the method across homes.

## Install the app on a phone

You need Xcode 26 or newer and an iPhone on iOS 26. You need XcodeGen 2.46.0 only to change `project.yml`.

1. Run `cp Config/Local.xcconfig.example Config/Local.xcconfig` in this folder.
2. In `Local.xcconfig`, set `DEVELOPMENT_TEAM` to your Team ID and `BUNDLE_ID_PREFIX` to a prefix your team can register. The app id becomes `<prefix>.measurelab`.
3. Open `MeasureLab.xcodeproj`, pick the iPhone, and run.

Git ignores `Local.xcconfig`. Leave the team field in Xcode's Signing & Capabilities tab empty. That field writes your team into `project.pbxproj`, and CI's drift check fails on it.

## What each tool does

The app tracks with ARKit and never uses LiDAR scene reconstruction. On a LiDAR phone, the Session sheet can record depth maps for comparison runs. That switch is off by default and locks after the first tap.

A tap counts only after tracking has been normal for 1 s. Interruptions restart that wait. A new session ignores pre-reset callbacks and waits for a fresh tracking cycle. Tap the camera image to mark a spot, or press **Mark** to mark under the center ring. **Freeze** holds one frame still for precise tapping.

| Tool | A tap | Refused when |
| --- | --- | --- |
| Ground | Finds the ground under the tap. The app flags hits past the edge of a found plane, hits on ARKit's estimated surface, and taps that look down less than 30°. | ARKit finds no ground there |
| Wall | The first two taps mark where the wall meets the ground, and the wall is the vertical plane through them. Later taps on the wall's base check that plane. A check tap with a flag of its own can't confirm the wall. | The two contacts are under 2 m apart, or the camera stands in line with the wall |
| On wall | Finds where the tap meets the newest wall. Reports the distance along the wall from the first contact and the height above the ground line. The app flags points beyond the contacts. A point also carries its wall's warnings: a flagged contact, no check yet, or a failed check. | The ray is more than 60° from straight on, or the wall is behind the camera |
| Two-view | Tap a feature, step sideways, and tap it again in a new frame. On a frozen second view, a dashed line shows where the feature must lie. | The views are under 15° apart, the rays miss by more than 2 in, or both taps are on one frame |
| Measure | Point to point gives straight, horizontal, height difference, and distance along a chosen wall. Point to wall gives facing gap and height above ground. Type the tape reading in feet and inches (`6`, `3 1/4`) to record app minus tape. Height, facing gap and along-wall distance are flagged when a point lies beyond the contacts of the wall they use. A failed wall check turns measurements already saved on that wall into abstentions. | |

These limits are the research note's gates. Each one is a hypothesis this run tests, not a calibrated value. Every session records the limits it used.

The operator must see the wall-ground contacts and tap the same physical feature in both two-view images. A ground-plane hit cannot prove that a shrub did not hide the intended contact. Different features can also satisfy the ray-angle and ray-gap checks. The app does not detect either mistake reliably, so an accepted measurement does not prove visibility or correspondence. Keep both required-abstention cases in the test. Either can fail the method's criteria. This experiment cannot establish production clearance safety.

A validation contact must lie on the base of the same visible, straight wall. It may lie outside the two initial contacts because it checks the wall's plane, not the supported span for later measurements.

## Run the hour

Bring an iPhone without LiDAR running this app, a 50 ft tape, a spirit level, chalk or painter's tape, and two people. One of them is the uncoached operator, who has not seen the tape values and gets no coaching. Stay on the ground. Use no ladders, and keep clear of gas fittings.

1. **Minutes 0 to 5: check the phone and the pixel mapping.** Open the Session sheet and confirm that LiDAR shows No and Mesh reconstruction shows Not supported. With Ground, mark a sharp paving joint, walk 2 m away, and check that the yellow dot still sits on the joint. Freeze a frame, tap the same joint, and check that the tap ring sits on it.
2. **Minutes 5 to 15: tape the ground truth.** Chalk two marks at the base of one straight wall, 30 ft apart. Out of the uncoached operator's sight, tape and write down these values:
   - the 30 ft span between the marks;
   - one door or window: the width, and the sill height above the ground at the wall;
   - the height of the electric meter's bottom edge above the ground;
   - the facing gap from the wall to a rigid fence or wall across from it, perpendicular to the wall, with chalk at both ends;
   - the lowest overhead near the wall, such as an eave corner or porch beam, as height above the ground below it;
   - a reference X chalked on paving 2 m from the wall.

   Pick the opening, meter, fence chalk and overhead between the two wall marks. The app flags wall readings beyond them as abstentions.
3. **Minutes 15 to 40: make three captures.** The coached operator makes two, and the uncoached operator makes one. Start each with Session › New session. In each capture:
   1. With Ground, mark the reference X.
   2. With Wall, mark the two chalk marks as contacts, then one more point on the wall's base as a validation contact.
   3. With On wall, mark both sides of the opening, its sill, and the meter's bottom edge.
   4. With Ground, mark the chalk at the fence base.
   5. With Two-view, mark the overhead corner, step about 1 m sideways, and mark it again.
   6. Walk to the far end of the wall and back, then mark the reference X again with Ground.
   7. Measure each taped quantity and type the tape value:
      - the 30 ft span, contact to contact along the wall;
      - the opening width, along the wall;
      - the sill and the meter, as height above ground against the wall;
      - the facing gap, as the fence point against the wall;
      - the overhead, as the two-view point's height above ground against the wall;
      - the return gap, as the straight distance between the two reference X points, with tape value 0 so the error reads directly.
   8. Note the capture time, from New session to the last measurement.
4. **Minutes 40 to 50: try to break it.** In a fourth session, try the required-abstention cases below. Each should end in a refusal, a flag or a failed check. Also record errors for the exploratory conditions; those do not require an abstention.
5. **Minutes 50 to 60: export and score.** Share each session's zip to a laptop and put it in `experiments/measure-lab/data/`, which git ignores. Fill in the sheet below from `measurements` and `refusals` in each session.json. To score against a full survey instead, use `score import-measure-lab` in `experiments/scoring` (PR #4).

## Record sheet

Copy this blank sheet into git-ignored `data/` and fill it in there. Never edit the tracked sheet with real tape readings or results.

Fill in one row per quantity. Errors are app minus tape, in inches.

| Quantity | Method | Tape | Captures (error, in) | Max | Median | Abstentions | Within bound |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 30 ft span | Wall contacts | | | | | | |
| Opening width | On wall | | | | | | |
| Sill height | On wall | | | | | | |
| Meter bottom height | On wall | | | | | | |
| Facing gap | Ground to wall | | | | | | |
| Overhead height | Two-view | | | | | | |
| Return to reference | Ground | | | | | | |

Exploratory conditions. Blank stucco and direct sun have no dedicated rejection rule. Record the measurement error and any warnings, whether or not the app abstains.

| Condition | Error and warnings |
| --- | --- |
| Wall point on blank stucco | |
| Capture in direct sun | |

Required-abstention cases. Each should end in a refusal, a flag or a failed check.

| Case | Result |
| --- | --- |
| Wall contact hidden by a shrub | |
| Two-view pair with a 20 cm step, on a feature at least 2 m away so the rays meet under 15° (about 6° at 2 m). The app has no step-length limit, so the 15° ray-angle gate is what should refuse it. | |
| Two-view pair where the second tap is on a different feature | |
| Wall built from a planter's base, then validated against the real wall base | |

Also record the uncoached operator's capture time, and the decision check at 3 ft on both sides of the threshold.
