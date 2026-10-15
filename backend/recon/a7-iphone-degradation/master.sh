#!/bin/bash
# Serial A7 sweep: for each condition render frames, run A1 on both scenes, then pi3 and DA3 on both scenes.
# Serial on purpose: running these concurrently on 4 cores thrashed (one matching block took 927 s vs ~100 s).
cd "$(dirname "$0")"; source ~/a2/venv/bin/activate
CONDS=${CONDS:-"dslr_ref base noise_iso1600 blur_16 combo_dusk combo_day noise_iso400 noise_iso6400 blur_8 blur_32 hdr_sharp rs_30dps jpeg_q25 heic_q30 noise_iso50 combo_dusk_slow rs_90dps blur_4 jpeg_q50 combo_night"}
for c in $CONDS; do
  for s in facade electro; do python degrade.py $s $c > /dev/null; done
  for s in facade electro; do
    [ -f results/$s/A1/$c.json ] || { echo "$(date +%T) A1 $s $c"; python a1run.py $s $c > ~/a7/logs/a1_${s}_$c.log 2>&1; tail -1 ~/a7/logs/a1_${s}_$c.log | cut -c1-200; }
  done
  for m in pi3 da3; do for s in facade electro; do
    [ -f results/$s/$m/$c.json ] || { echo "$(date +%T) $m $s $c"; python ffrun.py $m $s $c > ~/a7/logs/${m}_${s}_$c.log 2>&1; grep -E "^[0-9]{2}:" ~/a7/logs/${m}_${s}_$c.log; }
  done; done
  python surface.py > /dev/null 2>&1; python summarize.py > results/summary.md 2>&1
done
python surface.py > /dev/null 2>&1; python summarize.py > results/summary.md 2>&1
echo "$(date +%T) MASTER DONE"
