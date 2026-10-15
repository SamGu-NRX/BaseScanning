#!/usr/bin/env bash
# Score dense runs as OpenMVS finishes them: shared scorer (facade) + ETH3D evaluator extras (all scenes).
cd "$(dirname "$0")"
. ~/a1/venv/bin/activate
RES=/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/eval-harness/results/eth3d-facade/A1-colmap
align_of() { case $1 in gt) echo gt;; incremental|global) echo sim3;; *) echo rigid;; esac; }
while true; do
  busy=0
  for S in facade electro delivery_area; do
    for j in ~/a1/work/$S/mvs/*_r1.json; do
      [ -f "$j" ] || continue
      r=$(basename "$j" _r1.json)
      if [ $S = facade ] && [ ! -f $RES/${r}_dense/geomscore.json ]; then
        echo "== hscore $r $(date +%T)"; python hscore.py "$r" dense > ~/a1/logs/hscore_${r}_dense.log 2>&1; echo "   exit $?"; busy=1
      fi
      if [ ! -f ~/a1/work/$S/scores/${r}_dense.json ]; then
        echo "== extras $S $r $(date +%T)"
        python score.py "$S" "$r" "$(align_of $r)" --cloud ~/a1/work/$S/mvs/${r}_r1/scene_dense.ply > ~/a1/logs/extras_${S}_${r}_dense.log 2>&1; echo "   exit $?"; busy=1
      fi
    done
  done
  [ $busy = 0 ] && sleep 60
done
