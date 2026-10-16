#!/usr/bin/env bash
# Full A1 chain for one ETH3D scene after `prepare.py` and `sfm.py <scene> features`.
#   bash run_scene.sh facade [stage...]    stages: sparse dense score (default: all)
# Logs to ~/a1/logs/<scene>_<step>.log; continues past a failed step.
set -u
S=$1; shift
STAGES=${*:-sparse dense score}
cd "$(dirname "$0")"
. ~/a1/venv/bin/activate
L=~/a1/logs
step() { local name=$1; shift; echo "== $name $(date +%T)"; /usr/bin/time -v "$@" > "$L/${S}_$name.log" 2>&1; echo "   exit $? $(date +%T)"; }
RUNS="gt incremental global arkit_lidar_modern_s0_fixed arkit_lidar_modern_s0_prior arkit_nonlidar_modern_s0_fixed arkit_nonlidar_modern_s0_prior"
align_of() { case $1 in gt) echo gt;; incremental|global) echo sim3;; *) echo rigid;; esac; }
for st in $STAGES; do
  case $st in
    sparse)
      step gt python sfm.py "$S" gt
      step incremental python sfm.py "$S" incremental
      step global python sfm.py "$S" global
      step arkit_lidar python sfm.py "$S" arkit lidar_modern 0
      step arkit_nonlidar python sfm.py "$S" arkit nonlidar_modern 0
      step scale_study python scale_study.py "$S" incremental
      ;;
    dense)
      for r in $RUNS; do [ -d ~/a1/work/$S/sparse/$r ] && step "dense_$r" python densify.py "$S" "$r"; done
      ;;
    score)
      for r in $RUNS; do
        [ -d ~/a1/work/$S/sparse/$r ] || continue
        step "score_sparse_$r" python score.py "$S" "$r" "$(align_of $r)"
        ply=~/a1/work/$S/mvs/${r}_r1/scene_dense.ply
        [ -f "$ply" ] && step "score_dense_$r" python score.py "$S" "$r" "$(align_of $r)" --cloud "$ply"
      done
      ;;
  esac
done
echo "== done $(date +%T)"
