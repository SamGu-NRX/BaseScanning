#!/usr/bin/env bash
# Every C2 run behind internal/breadth/C2-volumetric.md: grids, sweeps, PR #11 comparison, verdicts,
# validation and scoring.
#   bash run_all.sh [WORK_DIR]
# Needs (see internal/handoffs/C2-volumetric.md "Re-create the VM state"): ETH3D facade, electro,
# delivery_area in ~/c2/data (+ *_dslr_occlusion for the GT scenes), a venv with numpy scipy shapely
# scikit-image numba plyfile pyyaml jsonschema matplotlib + internal/eval-harness/requirements.txt,
# PR #11 worktree at ~/c2/pr11 and its scene schema at ~/c2/pr13/scene.schema.json.
set -e
C=$(cd "$(dirname "$0")" && pwd)
H=$(cd "$C/../../eval-harness" && pwd)
W=${1:-$HOME/c2/work}
D=$HOME/c2/data
mkdir -p "$W"/{grids,sweep,pr11in,pr11,verdicts}
cd "$C"

# 1. occupancy grids (5 cm; laser, and laser geometry seen from the DSLR poses)
python -m c2vol.build scenes/eth3d-facade-wing.json $D/facade $W/grids/facade_laser.npz --mode laser
python -m c2vol.build scenes/eth3d-facade-wing.json $D/facade $W/grids/facade_photos_kf.npz --mode photos --cams keyframes
python -m c2vol.build scenes/eth3d-facade-wing.json $D/facade $W/grids/facade_photos_all.npz --mode photos --cams all
python -m c2vol.build scenes/eth3d-electro-doors.json $D/electro $W/grids/electro-doors_laser.npz --mode laser
python -m c2vol.build scenes/eth3d-delivery-garage.json $D/delivery_area $W/grids/delivery-garage_laser.npz --mode laser

# 2. GT scenes v0 (harness builders); delivery's floor is a drive
(cd "$H" && python eth3d/build_gt.py --data $D/facade --out $W/facade-gt) || \
  echo "build_gt.py failed; copy results/eth3d-facade/gt-laser/scene.json next to its rebuilt PLYs"
(cd "$H" && python eth3d/build_gt_c2.py --seed "$C/scenes/eth3d-delivery-garage.json" --data $D/delivery_area \
  --site eth3d-delivery --out $W/delivery-gt --ground-material g1=drive)

# 3. sweeps (both rule sets) and PR #11 on the converted scene
run() {  # scene grid [extra sweep args]
  local sc=$1 g=$2; shift 2
  for r in rules_pr11demo rules_strictest; do
    python -m c2vol.sweep scenes/$sc.json $W/grids/$g.npz $r.yaml $W/sweep "$@"
  done
  python tools/to_pr11.py scenes/$sc.json $W/grids/$g.npz $W/pr11in/$g.json --schema $HOME/c2/pr13/scene.schema.json > /dev/null
  for r in demo strict; do
    (cd $HOME/c2 && python "$C/tools/run_pr11.py" pr11/server $W/pr11in/$g.json $W/pr11/${g}_$r.json --rules $r)
  done
}
run eth3d-facade-wing facade_laser
run eth3d-facade-wing facade_photos_kf
run eth3d-facade-wing facade_photos_all
run eth3d-electro-doors electro-doors_laser
run eth3d-delivery-garage delivery-garage_laser --ground-from $W/delivery-gt/scene.json

# 4. verdicts (base-verdict/v0), validation and scoring
verdicts() {  # sweep-grid gt-scene out-stem
  python -m c2vol.verdict $W/sweep/$1_rules_pr11demo.json $2 rules_pr11demo.yaml demo $W/verdicts/$3.demo.json
  python -m c2vol.verdict $W/sweep/$1_rules_strictest.json $2 rules_strictest.yaml S $W/verdicts/$3.S.json
}
for g in facade_laser facade_photos_kf facade_photos_all; do verdicts $g $W/facade-gt/scene.json $g; done
verdicts delivery-garage_laser $W/delivery-gt/scene.json delivery
cd "$H"
python -m evalharness.cli validate $W/verdicts/*.json
for p in demo S; do
  python -m evalharness.cli score-verdicts --verdict $W/verdicts/delivery.$p.json \
    --labels ../criteria/labels-v0/eth3d-delivery.{A,B,C,S}.json --out $W/verdictscores
done
