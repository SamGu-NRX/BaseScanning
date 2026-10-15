#!/bin/bash
cd /cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth/a10-sparse-photo
PY=~/a2/venv/bin/python
r() { echo "$(date +%T) run $*"; $PY run.py "$@" 2>&1 | grep --line-buffered -E "^[0-9]{2}:[0-9]{2}:[0-9]{2} |Traceback|Killed"; }
h() { echo "$(date +%T) hyb $*"; $PY hybrid.py "$@" 2>&1 | grep --line-buffered -E "^[0-9]{2}:[0-9]{2}:[0-9]{2} |Traceback|Killed"; }
full() { r $1 $2 $3 colmap prior pi3; h $1 $2 $3 cda3 cpi3; }
base() { r $1 $2 $3 colmap prior; h $1 $2 $3 cda3; }
full facade dslr_ref N40_guided; base facade dslr_ref N40_careless0; h facade dslr_ref N40_careless0 cpi3
full facade dslr_ref N60_guided; base facade dslr_ref N60_careless0
full facade dslr_ref N70_guided; r facade dslr_ref N70_guided da3; base facade dslr_ref N70_careless0
full electro dslr_ref N40_guided; r electro dslr_ref N40_guided da3; base electro dslr_ref N40_careless0; full electro dslr_ref all45_guided
full delivery_area dslr_ref N40_guided; r delivery_area dslr_ref N40_guided da3; base delivery_area dslr_ref N40_careless0; full delivery_area dslr_ref all44_guided
base facade combo_day N40_guided; full facade combo_day N60_guided; base facade combo_day N70_guided; base facade combo_day N40_careless0
full electro combo_day N40_guided; base electro combo_day all45_guided
full delivery_area combo_day N40_guided; base delivery_area combo_day all44_guided
echo "$(date +%T) QUEUE2 DONE"
