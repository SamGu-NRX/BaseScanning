#!/bin/bash
# One ETH3D site end to end once its tight-prior A1 model exists.
#   bash run_site.sh facade|electro|delivery_area
set -x
S=$1; E=$HOME/e2e; . $E/venv/bin/activate; export HF_HOME=$E/hf PYTHONDONTWRITEBYTECODE=1
case $S in facade) SITE=eth3d-facade; G=facade;; electro) SITE=eth3d-electro; G=electro;; delivery_area) SITE=eth3d-delivery; G=delivery;; esac
GT=$HOME/data/eval-harness/$G-gt/scene.json
M=$HOME/e2e/a1w/$S/sparse/arkit_nonlidar_modern_s0_tight_prior
W=$E/work/$S
python $E/code/export_model.py $M $W/a1t
python $E/code/pi3_dense.py --model $M --images $HOME/data/eth3d/$S/images/dslr_images_undistorted --out $W/pi3t --cache $W/pi3cache \
  --per-view --min-agree 2 --rel-tol 0.01 > $E/logs/pi3t_$S.log 2>&1
python $E/code/export_model.py $M $W/a1t_pi3 --add $W/pi3t
for C in a1t a1t_pi3; do
  MODES="true arkit meterModel"; [ $C = a1t ] && MODES="true arkit meterModel meterP90"
  for mode in $MODES; do
    python $E/code/e2e_scene.py --gt $GT --recon $W/$C --mode $mode --out $W/$C/$mode > $E/logs/scene_${S}_${C}_$mode.log 2>&1
    bash $E/code/run_verdicts.sh $SITE $S $C.$mode $W/$C/$mode
  done
done
echo SITE_DONE $S
