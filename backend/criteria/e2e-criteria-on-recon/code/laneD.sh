E=$HOME/e2e; . $E/venv/bin/activate; export PYTHONDONTWRITEBYTECODE=1
S=delivery_area; M=$E/a1w/$S/sparse/arkit_nonlidar_modern_s0_tight_prior; W=$E/work/$S
while [ ! -f $W/pi3t/pi3_report.json ]; do sleep 30; done
python $E/code/export_model.py $M $W/a1t_pi3 --add $W/pi3t
for m in true arkit meterModel; do
  python $E/code/e2e_scene.py --gt $HOME/data/eval-harness/delivery-gt/scene.json --recon $W/a1t_pi3 --mode $m --out $W/a1t_pi3/$m > $E/logs/scene_${S}_a1t_pi3_$m.log 2>&1
  bash $E/code/run_verdicts.sh eth3d-delivery $S a1t_pi3.$m $W/a1t_pi3/$m
done
echo LANED_DONE
