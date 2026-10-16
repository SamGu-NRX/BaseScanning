#!/bin/bash
cd /cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth/a10-sparse-photo
PY=~/a2/venv/bin/python
until grep -q "LAMAR2 DONE" ~/a10/logs/queue_lamar2.log 2>/dev/null; do sleep 60; done
for h in yt-KSXy6LFwQU4 yt-LCPBf86oloE yt-Qm4b1cw8pV4; do $PY house.py $h 60 40 2>&1 | grep --line-buffered -E "^[0-9]{2}:[0-9]{2}:[0-9]{2} |Traceback"; done
echo "$(date +%T) HOUSES DONE"
