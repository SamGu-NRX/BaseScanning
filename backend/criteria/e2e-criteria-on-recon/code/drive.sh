for s in facade electro delivery_area; do
  while [ ! -f ~/e2e/a1w/$s/sparse/arkit_nonlidar_modern_s0_tight_prior.json ]; do sleep 20; done
  bash ~/e2e/code/run_site.sh $s > ~/e2e/logs/site_$s.log 2>&1 &
done
wait; echo DRIVE_DONE
