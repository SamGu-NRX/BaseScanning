#!/bin/bash
# Serial A10 grid (4 cores; parallel runs thrash). Skips finished rows.
cd /cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth/a10-sparse-photo
PY=~/a2/venv/bin/python
M="colmap prior pi3 da3"
run() { echo "$(date +%T) START $*"; $PY run.py "$@" 2>&1 | grep -E "^[0-9]{2}:[0-9]{2}:[0-9]{2} |Traceback|Error|Killed"; }
for s in N40_guided N40_careless0 N60_guided N60_careless0 N70_guided N70_careless0; do run facade dslr_ref $s $M; done
until grep -q GT_REST_DONE ~/a10/logs/gt_delivery.log 2>/dev/null; do sleep 30; done
for s in N40_guided N40_careless0 all45_guided; do run electro dslr_ref $s $M; done
for s in N40_guided N40_careless0 all44_guided; do run delivery_area dslr_ref $s $M; done
for s in N40_guided N60_guided N70_guided N40_careless0; do run facade combo_day $s $M; done
for s in N40_guided all45_guided; do run electro combo_day $s $M; done
for s in N40_guided all44_guided; do run delivery_area combo_day $s $M; done
echo "$(date +%T) QUEUE DONE"
