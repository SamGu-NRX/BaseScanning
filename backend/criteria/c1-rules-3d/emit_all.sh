#!/bin/bash
# Contract verdicts (official) for every scene x profile, plus C1-native-rule verdicts for comparison.
cd "$(dirname "$0")"
PY=/home/ubuntu/work/c1venv/bin/python
for sc in facade-M1 facade-M2 facade-M3 facade-contract-M1 facade-pr13cov-M1 harness-gt-laser electro-E1 electro-untyped-E1 delivery_area-D1 delivery_area-D2 delivery_area-D3; do
  $PY to_verdict.py outputs/scenes/$sc.json public A B C strictest 2>/dev/null &
done; wait
for sc in facade-M1 harness-gt-laser electro-untyped-E1; do $PY to_verdict.py outputs/scenes/$sc.json public A B C strictest --c1-rule 2>/dev/null; done
