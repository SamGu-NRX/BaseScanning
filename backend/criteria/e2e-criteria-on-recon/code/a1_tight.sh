set -x
. ~/e2e/venv/bin/activate
export A1_WORK=~/e2e/a1w A1_ETH3D=~/data/eth3d A1_PRIOR_SIGMA=0.03 A1_PRIOR_ROBUST=0 A1_TAG_SUFFIX=_tight PYTHONDONTWRITEBYTECODE=1
cd ~/e2e/store/internal/breadth/a1-classical
for s in facade electro delivery_area; do
  python prepare.py $s && python sfm.py $s features && python sfm.py $s gt && python sfm.py $s arkit nonlidar_modern 0
done
echo A1_DONE
