#!/bin/bash
cd ~/a2/code && source ~/a2/venv/bin/activate
for s in delivery_area electro facade; do
  while [ ! -f ~/a2/dl/done_${s}_dslr_undistorted.7z ]; do sleep 10; done
  echo "$(date +%T) prepare $s"
  python -m a2ff.peak a2ff.prepare $s
  touch ~/a2/prepared_$s
done
echo PREP_DONE
