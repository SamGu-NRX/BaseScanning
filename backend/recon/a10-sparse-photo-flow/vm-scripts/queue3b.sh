#!/bin/bash
cd /cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth/a10-sparse-photo
PY=~/a2/venv/bin/python
until grep -q "QUEUE2 DONE" ~/a10/logs/queue2b.log 2>/dev/null; do sleep 60; done
T="a9_med=0.006 a9_p90=0.020 weak_p90=0.047"
for x in "facade dslr_ref N40_guided" "facade dslr_ref N60_guided" "facade dslr_ref N70_guided" "electro dslr_ref N40_guided" "delivery_area dslr_ref N40_guided" "facade combo_day N60_guided"; do
  for m in colmap cda3; do echo "$(date +%T) rescale $x $m"; $PY rescale.py $x $m $T 2>&1 | grep --line-buffered -E "^(facade|electro|delivery)|Traceback|Error"; done
done
echo "$(date +%T) QUEUE3 DONE"
