# Drift anatomy: methods and full results

The README is the front door. This file keeps the method as it was pre-registered in 397507f, and the full result as the run reported it.

## Question

The server allows 0.16 ft of error per foot along the wall. That allowance dominates a check's error bar beyond a few feet, so which part of ARKit's error is worth attacking? The data is MARViN (iPhone 14 Pro Max, bar and church, walks within 10% of the reference's scale) and ADVIO 20–22 (iPhone 6s), using the evals' windows.

- **A1, return to the meter.** When a walk comes back within 0.3 m of an earlier point more than 30 s later, ARKit's closure residual r is the error in the displacement between the two visits. Does r bound the largest error between the visits?
- **A2, scale.** Is ARKit's scale one number per walk? This measures the spread of scale over sliding 5 m windows within a walk.
- **A3, the wall plane.** What share of the error runs across the direction of travel, which a wall plane would remove, compared with along it?
- **A4, a range to the meter.** A second phone left at the meter ranges the walking phone by UWB (σ 10 cm, simulated) at every window end. What is the p90 left over? Each window is one session that starts at the meter: the walking phone leaves the meter at the window's start, the second phone stays there, and the range runs from that start to the window's end. Sliding the start along a walk samples many meter placements; the error depends on the path walked from the meter, not on where in the dataset the meter sits.

## Pass criteria, fixed before the run

- **A1.** Across loops, the largest error between visits is at most max(2r, 2 in) in at least 90% of loops, so r can set a per-session error bar. Needs at least 8 loops.
- **A2.** One reference object near the meter is enough if the within-walk spread is under 1% (1 SD). Drop that idea if it is over 3%.
- **A3.** A wall-plane constraint is worth building if the along-travel p90 is at most 60% of the total p90 at 20 ft.
- **A4.** The p90 at 20 and 30 ft is at most 6 in.

## Result

All four fail. Tables: [results/drift_anatomy.md](results/drift_anatomy.md).

- **A1.** 30 of 56 MARViN loops (54%). ADVIO has no revisit under 0.3 m while ARKit tracks. Scale and heading error cancel on return, so r misses them.
- **A2.** 5.7% SD on MARViN, 19% on ADVIO: drop. A few outlier windows inflate MARViN's SD; its robust SD is 1.8%.
- **A3.** At 20 ft, along is 0.92 of the p90 on MARViN and 0.76 on ADVIO. Added after review: across-travel is a wall's normal only while the walk runs straight along the wall, and the walks have no wall annotations. On windows whose path stays within 0.25 m of its start-to-end line, along is 0.98 of the p90 on MARViN and 0.75 on ADVIO at 20 ft; within 0.5 m, 0.95 and 0.75. The conclusion holds on the windows that test it.
- **A4.** MARViN goes from 10.4 and 13.6 in to 7.7 and 8.5 in. At σ 10 cm the range's own p90 is 6.5 in, so the bar was nearly out of reach. At 5 cm: 4.9 and 6.1 in.

**Run:** `uv run python run.py`. Needs the evals checkout at 190ed33 (`EVALS_HARNESS`) and MARViN and ADVIO in `~/house-scanning-data`.

**What it changes:** the error worth attacking runs along travel. A range to the meter reduces it, a wall plane wouldn't, and r can't bound a session.
