#!/bin/bash
cd /cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth/a10-sparse-photo
PY=~/a2/venv/bin/python
for h in yt-KSXy6LFwQU4 yt-LCPBf86oloE yt-Qm4b1cw8pV4; do $PY house.py $h 60 40 2>&1 | grep --line-buffered -E "^[0-9]{2}:[0-9]{2}:[0-9]{2} |Traceback"; done
echo "$(date +%T) HOUSES DONE"
until grep -q "QUEUE2 DONE" ~/a10/logs/queue2b.log 2>/dev/null; do sleep 30; done
for s in N40_guided N60_guided N70_guided; do $PY rescore.py facade combo_day $s colmap prior cda3 cpi3 pi3; done
$PY rescore.py electro combo_day N40_guided colmap prior cda3
echo "$(date +%T) RESCORE DONE"
