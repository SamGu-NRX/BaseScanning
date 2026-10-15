#!/usr/bin/env bash
# ETH3D facade validation: GT, COLMAP scenes (oracle + simulated non-LiDAR ARKit), geometry scores.
# Paths are repo-relative. Run from anywhere; PY defaults to python.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
PY=${PY:-python}
D=${D:-$ROOT/.fixtures/eval-harness}
DATA=${DATA:-$ROOT/.fixtures/eth3d/facade}
ETH3D=${ETH3D:-$ROOT/.fixtures/eth3d}
R=${R:-$ROOT/.fixtures/eval-harness/results/eth3d-facade}
G=$D/facade-gt/scene.json
[ -f $G ] || $PY -m server.eval.eth3d.build_gt --data $DATA --out $D/facade-gt
M=$D/facade-colmap/sparse/best
[ -d $M ] || { mkdir -p $D/facade-colmap && tar -xzf $R/colmap-sparse-best.tar.gz -C $D/facade-colmap; }
$PY -m server.eval.eth3d.scene_from_recon --gt $G --model $M --out $D/facade-colmap-oracle --oracle > $D/oracle.log
$PY -m server.eval.eth3d.scene_from_recon --gt $G --model $M --out $D/facade-colmap-nonlidar --profile nonlidar_modern --seed 0 > $D/nonlidar.log
$PY -m server.eval.cli score-geometry --gt $G --recon $G --out $D/score-self
$PY -m server.eval.cli score-geometry --gt $G --recon $D/facade-colmap-oracle/scene.json --out $D/score-oracle
$PY -m server.eval.cli score-geometry --gt $G --recon $D/facade-colmap-nonlidar/scene.json --out $D/score-nonlidar
[ -f $D/facade-colmap/sparse_points.ply ] || $PY -c "
import pycolmap, numpy as np; from server.eval.sceneio import write_ply
r = pycolmap.Reconstruction('$M'); write_ply('$D/facade-colmap/sparse_points.ply', np.array([p.xyz for p in r.points3D.values()], np.float32))"
$PY -m server.eval.cli score-cloud --gt $G --cloud $D/facade-colmap/sparse_points.ply --cameras $M --producer A1-colmap-sparse-cloud --out $D/score-cloud-colmap
for n in self oracle nonlidar cloud-colmap; do mkdir -p $R/geomscore-${n#cloud-}; cp $D/score-$n/geomscore.{json,md} $R/geomscore-${n#cloud-}/ ; done
mkdir -p $R/gt-laser && cp $D/facade-gt/{scene.json,build_report.json,coverage.npz} $R/gt-laser/
for n in oracle nonlidar; do mkdir -p $R/colmap-$n && cp $D/facade-colmap-$n/{scene.json,build_report.json,coverage.npz} $R/colmap-$n/; done

# C1 (PR #11 via the adapter) on each scene, ground at 80 deg (default) and 75 deg, scored against labels.
L=${LABELS:-$ROOT/data/fixtures/labels}
mkdir -p $R/verdicts $R/verdictscores
for n in gt:$D/facade-gt/scene.json oracle:$D/facade-colmap-oracle/scene.json nonlidar:$D/facade-colmap-nonlidar/scene.json; do
  k=${n%%:*}; s=${n#*:}
  $PY -m server.eval.cli run-c1 --scene $s --out $R/verdicts/$k.C1.g80.json
  $PY -m server.eval.cli run-c1 --scene $s --out $R/verdicts/$k.C1.g75.json --ground-rule angle75
  $PY -m server.eval.cli run-c1 --scene $s --out $R/verdicts/$k.C1.fp3.json --ground-rule footprint3in
  for g in g80 g75 fp3; do
    $PY -m server.eval.cli score-verdicts --verdict $R/verdicts/$k.C1.$g.json \
      --labels $ROOT/server/eval/labels/eth3d-facade.demo.labels.json $L/eth3d-facade.A.json $L/eth3d-facade.B.json $L/eth3d-facade.C.json $L/eth3d-facade.S.json \
      --out $R/verdictscores
  done
done

# ETH3D electro and delivery_area: GT scenes seeded from the C2 worker's labelled scenes, then C1.
C2S=${C2S:-$ROOT/data/fixtures/scenes}
for n in electro:eth3d-electro-doors:eth3d-electro: delivery_area:eth3d-delivery-garage:eth3d-delivery:g1=drive; do
  IFS=: read -r data seedname site gm <<< "$n"
  k=${site#eth3d-}
  [ -f $D/$k-gt/scene.json ] || $PY -m server.eval.eth3d.build_gt_c2 --seed $C2S/$seedname.json --data $ETH3D/$data --site $site --out $D/$k-gt ${gm:+--ground-material $gm}
  RS=$ROOT/.fixtures/eval-harness/results/$site; mkdir -p $RS/gt-laser $RS/verdicts $RS/verdictscores
  cp $D/$k-gt/{scene.json,build_report.json,coverage.npz} $RS/gt-laser/
  $PY -m server.eval.cli run-c1 --scene $D/$k-gt/scene.json --out $RS/verdicts/gt.C1.g80.json
  $PY -m server.eval.cli run-c1 --scene $D/$k-gt/scene.json --out $RS/verdicts/gt.C1.g75.json --ground-rule angle75
  $PY -m server.eval.cli run-c1 --scene $D/$k-gt/scene.json --out $RS/verdicts/gt.C1.fp3.json --ground-rule footprint3in
  LS=$(ls $L/$site.*.json 2>/dev/null || true)
  if [ -n "$LS" ]; then for g in g80 g75 fp3; do $PY -m server.eval.cli score-verdicts --verdict $RS/verdicts/gt.C1.$g.json --labels $LS --out $RS/verdictscores; done; fi
done
