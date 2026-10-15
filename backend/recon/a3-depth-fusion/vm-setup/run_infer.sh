#!/bin/bash
# Per-image depth for every scene, in priority order. Resumes per view.
cd /cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth/a3-depth-fusion
. /home/ubuntu/a3/venv/bin/activate
export PYTHONPATH=/home/ubuntu/a3/stubs
F="warn|autocast|xformers|EdgeGuided"
run() { python infer.py --scene $1 --model $2 ${3:+--limit $3} 2>&1 | grep --line-buffered -v -i -E "$F"; }
for m in moge2-vits unidepth2-vits metric3d2-vits; do run facade $m; done
run electro moge2-vitl
for m in moge2-vits unidepth2-vits metric3d2-vits; do run delivery_area $m; done
run facade moge2-vitl
run delivery_area moge2-vitl
run electro depthpro
run facade depthpro
run delivery_area depthpro
echo INFER_ALL_DONE
