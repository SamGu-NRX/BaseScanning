#!/bin/bash
# Criteria on one scene: C1 (PR #11 via run-c1, demo rules), C1 rules-3d (A, B, C, S), C2 (demo, S),
# then score-verdicts against every matching label file.
#   bash run_verdicts.sh SITE SCENE_KEY TAG SCENE_DIR
# SITE eth3d-facade|eth3d-electro|eth3d-delivery; SCENE_KEY facade|electro|delivery_area; TAG gt|true|arkit|noTape
SITE=$1; SC=$2; TAG=$3; SDIR=$4
E=$HOME/e2e; ST=$E/store/internal; HAR=$ST/eval-harness; C1D=$ST/breadth/c1-rules-3d; C2D=$ST/breadth/c2-volumetric
. $E/venv/bin/activate; export PYTHONDONTWRITEBYTECODE=1
V=$E/work/$SC/verdicts/$TAG; mkdir -p $V/c13d $V/c2sweep $V/scores
case $SC in facade) C2S=eth3d-facade-wing; GT=$HOME/data/eval-harness/facade-gt/scene.json;;
  electro) C2S=eth3d-electro-doors; GT=$HOME/data/eval-harness/electro-gt/scene.json;;
  delivery_area) C2S=eth3d-delivery-garage; GT=$HOME/data/eval-harness/delivery-gt/scene.json;; esac
GF=""; [ $SC = delivery_area ] && GF="--ground-from $GT"
cd $HAR
python -m evalharness.cli run-c1 --scene $SDIR/scene.json --out $V/C1pr11.demo.json > $V/C1pr11.log 2>&1 || echo "run-c1 failed" >> $V/errors.txt
cd $C1D
python from_base_scene.py $SDIR/scene.json $V/scene3d.json > /dev/null && python to_verdict.py $V/scene3d.json A B C strictest --out $V/c13d > $V/c13d.log 2>&1 || echo "c1-3d failed" >> $V/errors.txt
cd $C2D
if [ $TAG = gt ]; then
  G=$E/work/$SC/c2laser/grid.npz; C2J=$C2D/scenes/$C2S.json; IDX=$GT
  if [ ! -f $G ]; then mkdir -p $(dirname $G); python -m c2vol.build $C2J $HOME/data/eth3d/$SC $G --mode laser > $(dirname $G)/build.log 2>&1; fi
else
  mkdir -p $SDIR/c2
  [ -f $SDIR/c2/grid.npz ] || python $E/code/c2_recon.py --c2scene $C2D/scenes/$C2S.json --gt $GT --e2e $SDIR --out $SDIR/c2 > $SDIR/c2/build.log 2>&1
  G=$SDIR/c2/grid.npz; C2J=$SDIR/c2/c2scene.json; IDX=$SDIR/c2/index_scene.json
  python $E/code/c2_index_scene.py --gt $GT --e2e $SDIR --out $IDX > /dev/null || echo "c2 index failed" >> $V/errors.txt
fi
for r in rules_pr11demo rules_strictest; do
  python -m c2vol.sweep $C2J $G $r.yaml $V/c2sweep $GF > $V/c2sweep/$r.log 2>&1 || echo "c2 sweep $r failed" >> $V/errors.txt
done
python -m c2vol.verdict $V/c2sweep/grid_rules_pr11demo.json $IDX rules_pr11demo.yaml demo $V/C2.demo.json > $V/c2v_demo.log 2>&1 || echo "c2 verdict demo failed" >> $V/errors.txt
python -m c2vol.verdict $V/c2sweep/grid_rules_strictest.json $IDX rules_strictest.yaml S $V/C2.S.json > $V/c2v_S.log 2>&1 || echo "c2 verdict S failed" >> $V/errors.txt
cd $HAR
LAB="$ST/criteria/labels-v0/$SITE.A.json $ST/criteria/labels-v0/$SITE.B.json $ST/criteria/labels-v0/$SITE.C.json $ST/criteria/labels-v0/$SITE.S.json"
[ -f labels/$SITE.demo.labels.json ] && LAB="$LAB $HAR/labels/$SITE.demo.labels.json"
for f in $V/C1pr11.demo.json $V/c13d/*.json $V/C2.demo.json $V/C2.S.json; do
  [ -f $f ] || continue
  python -m evalharness.cli validate $f >> $V/validate.log 2>&1 || true
  python -m evalharness.cli score-verdicts --verdict $f --labels $LAB --out $V/scores >> $V/scores.log 2>&1 || echo "score $f failed" >> $V/errors.txt
done
echo DONE $SITE $TAG
