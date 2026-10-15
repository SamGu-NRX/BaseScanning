#!/bin/bash
# bash queue.sh a1|gen   -- runs every condition in priority order for facade then electro.
cd "$(dirname "$0")"; source ~/a2/venv/bin/activate
CONDS=${CONDS:-"dslr_ref base noise_iso1600 blur_16 combo_dusk combo_day noise_iso6400 blur_8 blur_32 hdr_sharp rs_30dps rs_90dps jpeg_q25 heic_q30 jpeg_q50 noise_iso50 noise_iso400 blur_4 combo_dusk_slow combo_night"}
for c in $CONDS; do for s in facade electro; do
  case $1 in
    gen) python degrade.py $s $c > /dev/null ;;
    a1) [ -f results/$s/A1/$c.json ] || { echo "$(date +%T) A1 $s $c"; python a1run.py $s $c > ~/a7/logs/a1_${s}_$c.log 2>&1; tail -1 ~/a7/logs/a1_${s}_$c.log | cut -c1-400; } ;;
  esac
done; done

# ff: loop until every model x scene x condition has a result (picks up frames as `gen` renders them)
if [ "$1" = ff ]; then
  for round in $(seq 1 40); do
    for m in pi3 da3; do for s in facade electro; do
      python ffrun.py $m $s $CONDS 2>&1 | grep -E "^[0-9]{2}:[0-9]{2}|Error|error" ; done; done
    n=$(ls results/*/pi3/*.json results/*/da3/*.json 2>/dev/null | wc -l); echo "$(date +%T) ff round $round: $n results"
    [ "$n" -ge $(( $(echo $CONDS | wc -w) * 4 )) ] && break; sleep 60
  done
fi
echo "$(date +%T) QUEUE $1 DONE"
