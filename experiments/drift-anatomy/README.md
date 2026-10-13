# Drift anatomy

## Question

The server allows 0.16 ft of error per foot along the wall, which dominates a check's error bar beyond a few feet. Which part of ARKit's error is worth attacking? A1 asks whether returning to the meter bounds it, A2 whether scale is one number per walk, A3 how much runs across travel, where a wall plane would help, and A4 what a UWB range to the meter leaves. Each window stands for a session that starts at the meter, so in A4 the second phone sits at the window's start. The data is MARViN (iPhone 14 Pro Max) and ADVIO (iPhone 6s); [METHODS.md](METHODS.md) has the details.

## Pass criteria, fixed before the run (397507f)

- **A1.** Across loops, the largest error between visits is at most max(2r, 2 in) in at least 90% of loops, so r can set a per-session error bar. Needs at least 8 loops.
- **A2.** One reference object near the meter is enough if the within-walk spread is under 1% (1 SD). Drop that idea if it is over 3%.
- **A3.** A wall-plane constraint is worth building if the along-travel p90 is at most 60% of the total p90 at 20 ft.
- **A4.** The p90 at 20 and 30 ft is at most 6 in.

## Result

All four fail ([tables](results/drift_anatomy.md)).

- **A1.** The closure residual bounds the peak error in 30 of 56 loops. Scale and heading error cancel on return.
- **A2.** Scale spread within a walk is 1.8% robust SD (5.7% SD) on MARViN.
- **A3.** At 20 ft, the along-travel p90 is 9.6 of 10.4 in. On the windows that walk straight, which is where across-travel is a wall's normal, along is 0.95 to 0.98 of the total on MARViN (added after review).
- **A4.** UWB cuts 20 ft from 10.4 to 7.7 in at σ 10 cm, and to 4.9 in at σ 5 cm.

**Run:** `uv run python run.py`: 20 s. Needs the evals checkout at 190ed33 and MARViN and ADVIO in `~/house-scanning-data`.

**What it changes:** the error worth attacking runs along travel. Measuring a span inside one photo avoids it. A wall plane, a return to the meter or one ruler at the meter don't.
