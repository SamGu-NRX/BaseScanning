#!/bin/bash
# Phase 2, after phase 1: VGGT, MapAnything 392 px, more metadata profiles, remaining seeds, houses, 16 views.
cd ~/a2/code && source ~/a2/venv/bin/activate
while ! grep -q GRID1_DONE ~/a2/grid1.log; do sleep 20; done
R="python -m a2ff.peak a2ff.run"
S="delivery_area electro facade"
while pgrep -f "a2ff.harness_run --model pi3" >/dev/null; do sleep 10; done
HR="python -m a2ff.peak a2ff.harness_run"
$HR --model da3large --modes images KT:exact KT:nonlidar_modern.draw0
$HR --model mapanything --modes images KT:exact KT:nonlidar_modern.draw0
$HR --model vggt --modes images
touch ~/a2/harness_done
$R --model vggt --scene $S --sizes 8 --seeds 8 --modes images
$R --model mapanything392 --scene $S --sizes 8 --seed-index 0 2 4 6 --modes images KT:exact
$R --model mapanything --scene $S --sizes 8 --seed-index 0 2 4 6 --modes KT:lidar_modern.draw0 KT:nonlidar_2018.draw0 K:nonlidar_modern.draw0
$R --model da3large --scene $S --sizes 8 --seeds 8 --modes KT:lidar_modern.draw0 KT:nonlidar_2018.draw0 KT:nonlidar_modern.draw1 KT:nonlidar_modern.draw2
touch ~/a2/grid2a_done
H="yt-0gotY_syWWU yt-4h8kyjnPo9s yt-5-M-7f35Tyg"
python -m a2ff.peak a2ff.house --model pi3 --house $H --frames 24
python -m a2ff.peak a2ff.house --model mapanything --house $H --frames 24
python -m a2ff.peak a2ff.house --model da3large --house $H --frames 24
touch ~/a2/houses_done
$R --model mapanything --scene $S --sizes 8 --seed-index 1 3 5 7 --modes images KT:exact KT:nonlidar_modern.draw0
$R --model pi3 --scene $S --sizes 16 --seeds 8 --modes images
$R --model da3large --scene $S --sizes 16 --seeds 8 --modes images KT:nonlidar_modern.draw0
echo GRID2_DONE
