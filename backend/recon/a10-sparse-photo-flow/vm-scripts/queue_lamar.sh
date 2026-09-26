#!/bin/bash
cd /cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth/a10-sparse-photo
PY=~/a2/venv/bin/python
for s in 2022-02-10_14.47.24_s0 2022-06-17_10.43.29_s0 2022-02-10_14.47.24_s1; do
  for set in N40_guided N60_guided N70_guided; do $PY lamar_a10.py $s $set colmap prior cda3 2>&1 | grep --line-buffered -E "^[0-9]{2}:[0-9]{2}:[0-9]{2} lamar|Traceback"; done
  $PY lamar_a10.py $s N40_careless colmap prior 2>&1 | grep --line-buffered -E "^[0-9]{2}:[0-9]{2}:[0-9]{2} lamar|Traceback"
done
echo "$(date +%T) LAMAR QUEUE DONE"
