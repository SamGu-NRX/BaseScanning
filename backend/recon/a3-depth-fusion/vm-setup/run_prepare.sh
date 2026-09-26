#!/bin/bash
. /home/ubuntu/a3/venv/bin/activate
export HOUSE_SCANNING_DATA=/home/ubuntu/a3/data
cd /home/ubuntu/a3/hs/experiments/evals
for s in electro delivery_area facade; do
  python -c "
import time; t=time.time()
from evals.recon import prepare
prepare('$s')
print('prepare $s', round(time.time()-t), 's')
"
done
echo PREPARE_DONE
