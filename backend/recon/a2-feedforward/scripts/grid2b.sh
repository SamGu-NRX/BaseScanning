#!/bin/bash
cd ~/a2/code && source ~/a2/venv/bin/activate
while pgrep -f "a2ff.harness_run --model mapanything" >/dev/null; do sleep 10; done
R="python -m a2ff.peak a2ff.run"; S="delivery_area electro facade"
$R --model da3large --scene $S --sizes 8 --seeds 8 --modes KT:lidar_modern.draw0 KT:nonlidar_2018.draw0 KT:nonlidar_modern.draw1 KT:nonlidar_modern.draw2 KT:lidar_modern.draw1 KT:lidar_modern.draw2
touch ~/a2/da3_extra_done
H="yt-0gotY_syWWU yt-4h8kyjnPo9s yt-5-M-7f35Tyg"
python -m a2ff.peak a2ff.house --model pi3 --house $H --frames 16
python -m a2ff.peak a2ff.house --model da3large --house $H --frames 16
python -m a2ff.peak a2ff.house --model mapanything --house $H --frames 16
touch ~/a2/houses_done
$R --model mapanything --scene $S --sizes 8 --seed-index 0 2 4 6 --modes KT:lidar_modern.draw0 KT:nonlidar_2018.draw0 K:nonlidar_modern.draw0
touch ~/a2/ma_extra_done
$R --model vggt --scene $S --sizes 8 --seeds 8 --modes images
python -m a2ff.peak a2ff.harness_run --model vggt --modes images
touch ~/a2/vggt_done
$R --model mapanything392 --scene $S --sizes 8 --seed-index 0 2 4 6 --modes images KT:exact
$R --model pi3tall --scene $S --sizes 8 --seeds 8 --modes images
$R --model pi3 --scene $S --sizes 16 --seeds 8 --modes images
$R --model da3large --scene $S --sizes 16 --seeds 8 --modes images KT:nonlidar_modern.draw0
echo GRID2B_DONE
