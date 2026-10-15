# C1 (PR #11 demo) and C1 rules-3d (A B C S) on a walkaround scene; no labels exist for these houses.
#   bash house_verdicts.sh SCENE_DIR
set -e
D=$1; E=$HOME/e2e; . $E/venv/bin/activate; export PYTHONDONTWRITEBYTECODE=1
V=$D/verdicts; mkdir -p $V/c13d
cd $E/store/internal/eval-harness; python -m evalharness.cli run-c1 --scene $D/scene.json --out $V/C1pr11.demo.json > $V/C1pr11.log 2>&1 || echo run-c1 failed
cd $E/store/internal/breadth/c1-rules-3d; python from_base_scene.py $D/scene.json $V/scene3d.json > /dev/null && python to_verdict.py $V/scene3d.json A B C strictest --out $V/c13d > $V/c13d.log 2>&1 || echo c13d failed
cd $E/store/internal/eval-harness; python -m evalharness.cli validate $V/C1pr11.demo.json $V/c13d/*.json
