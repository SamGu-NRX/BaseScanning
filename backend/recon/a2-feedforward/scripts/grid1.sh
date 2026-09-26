#!/bin/bash
# Phase 1: commercially usable models, 3 scenes, 8-view groups.
cd ~/a2/code && source ~/a2/venv/bin/activate
R="python -m a2ff.peak a2ff.run"
for s in delivery_area electro facade; do
  while [ ! -f ~/a2/prepared_$s ]; do sleep 15; done
  echo "=== $(date +%T) $s"
  $R --model pi3 --scene $s --sizes 8 --seeds 8 --modes images
  $R --model da3large --scene $s --sizes 8 --seeds 8 --modes images KT:exact KT:nonlidar_modern.draw0
  $R --model mapanything --scene $s --sizes 8 --seed-index 0 2 4 6 --modes images K:exact KT:exact KT:nonlidar_modern.draw0
  touch ~/a2/grid1_$s
done
echo GRID1_DONE
