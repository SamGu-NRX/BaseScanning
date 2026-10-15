#!/bin/bash
source ~/a2/venv/bin/activate
cd "$(dirname "$0")"
for s in 2022-06-17_10.43.29_s1 2022-02-10_14.47.24_s0 2022-02-10_14.47.24_s1 2021-06-02_11.12.32_s0; do
  python a1_lamar.py $s fixed prior_s03 gt
done
for s in 2022-06-17_10.43.29_s1 2022-02-10_14.47.24_s0 2022-02-10_14.47.24_s1 2021-06-02_11.12.32_s0; do
  python a1_lamar.py $s noprior prior
done
echo A1_ALL_DONE
