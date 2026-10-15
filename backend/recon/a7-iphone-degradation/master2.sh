#!/bin/bash
# Two-lane A7 sweep. MODE=a1: render frames + A1 on both scenes. MODE=ff: pi3 + DA3, waiting for frames the
# a1 lane renders (only that lane renders, so two processes never write the same condition).
# Two lanes because A1's RANSAC-heavy matching and the mostly single-threaded shared scorer overlap well.
cd "$(dirname "$0")"; source ~/a2/venv/bin/activate; export A7_SKIP_OWNPOSE_SCORE=${A7_SKIP_OWNPOSE_SCORE-1}
CONDS=${CONDS:-"dslr_ref base noise_iso1600 blur_16 combo_dusk combo_day noise_iso400 noise_iso6400 blur_8 blur_32 hdr_sharp rs_30dps jpeg_q25 heic_q30 noise_iso50 combo_dusk_slow rs_90dps blur_4 jpeg_q50 combo_night"}
for c in $CONDS; do
  if [ "$MODE" = a1 ]; then
    for s in facade electro; do python degrade.py $s $c > /dev/null; done
    for s in facade electro; do
      [ -f results/$s/A1/$c.json ] || { echo "$(date +%T) A1 $s $c"; python a1run.py $s $c > ~/a7/logs/a1_${s}_$c.log 2>&1; tail -1 ~/a7/logs/a1_${s}_$c.log | cut -c1-200; }
    done
  else
    until [ -f ~/a7/work/facade/$c/frames.json ] && [ -f ~/a7/work/electro/$c/frames.json ]; do sleep 30; done
    for m in pi3 da3; do for s in facade electro; do
      [ -f results/$s/$m/$c.json ] || { echo "$(date +%T) $m $s $c"; python ffrun.py $m $s $c > ~/a7/logs/${m}_${s}_$c.log 2>&1; grep -E "^[0-9]{2}:" ~/a7/logs/${m}_${s}_$c.log; }
    done; done
  fi
  python surface.py > /dev/null 2>&1; python summarize.py > results/summary.md 2>&1
done
echo "$(date +%T) LANE $MODE DONE"
