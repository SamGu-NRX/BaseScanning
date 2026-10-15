#!/bin/bash
cd /cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth/a10-sparse-photo
PY=~/a2/venv/bin/python
r() { echo "$(date +%T) run $*"; $PY run.py "$@" 2>&1 | grep --line-buffered -E "^[0-9]{2}:[0-9]{2}:[0-9]{2} |Killed"; }
h() { echo "$(date +%T) hyb $*"; $PY hybrid.py "$@" 2>&1 | grep --line-buffered -E "^[0-9]{2}:[0-9]{2}:[0-9]{2} |Killed"; }
full() { r $1 $2 $3 colmap prior pi3; h $1 $2 $3 cda3 cpi3; }
base() { r $1 $2 $3 colmap prior; h $1 $2 $3 cda3; }
h facade dslr_ref N70_guided cpi3; base facade dslr_ref N70_careless0
full electro dslr_ref N40_guided; base electro dslr_ref N40_careless0; base electro dslr_ref all45_guided
full delivery_area dslr_ref N40_guided; base delivery_area dslr_ref N40_careless0; base delivery_area dslr_ref all44_guided
base facade combo_day N40_guided; full facade combo_day N60_guided; base facade combo_day N70_guided
base electro combo_day N40_guided; base delivery_area combo_day N40_guided
r facade dslr_ref N70_guided da3
echo "$(date +%T) QUEUE2 DONE"
