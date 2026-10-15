# lane A: sparse A1 (tight priors) scenes + verdicts, all sites and modes
# lane B: A1 + pi3 fused, once each site's pi3t finishes
E=$HOME/e2e; . $E/venv/bin/activate; export PYTHONDONTWRITEBYTECODE=1
gtof() { case $1 in facade) echo eth3d-facade facade;; electro) echo eth3d-electro electro;; delivery_area) echo eth3d-delivery delivery;; esac; }
one() {  # S C mode
  set -- $1 $2 $3 $(gtof $1); S=$1; C=$2; m=$3; SITE=$4; G=$5; W=$E/work/$S
  python $E/code/e2e_scene.py --gt $HOME/data/eval-harness/$G-gt/scene.json --recon $W/$C --mode $m --out $W/$C/$m > $E/logs/scene_${S}_${C}_$m.log 2>&1
  bash $E/code/run_verdicts.sh $SITE $S $C.$m $W/$C/$m
}
if [ "$1" = A ]; then
  for S in facade electro delivery_area; do for m in true arkit meterModel meterP90; do one $S a1t $m; done; done
else
  for S in facade electro delivery_area; do
    M=$HOME/e2e/a1w/$S/sparse/arkit_nonlidar_modern_s0_tight_prior
    while [ ! -f $E/work/$S/pi3t/pi3_report.json ]; do sleep 30; done
    python $E/code/export_model.py $M $E/work/$S/a1t_pi3 --add $E/work/$S/pi3t
    for m in true arkit meterModel; do one $S a1t_pi3 $m; done
  done
fi
echo LANE_DONE $1
