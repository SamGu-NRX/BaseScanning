# Comparator: study model vs shipped solvers

- main: `c813182a808c957ccc981fa56490c6c29d3c1560`
- t3/server: `1d1e7e1b55237ae82f5af10125ca33818f15cfce`

`current_bar` in [models.py](models.py) is asserted equal to every on-wall
clearance bar both trees emit, to 1e-9. Refs agree: True.

The shipped bar decomposes as the study's `current` bar: the checked item's
default error (tap base plus drift at its farthest edge) plus the battery's
error at its farthest edge. The t3/server tree's driveway rows also charge
the meter's own 0.3 ft error to the battery's plan placement (see the
docstring); main emits no measured driveway rows on these scenes. The gas
and driveway rows pin the item side at the item's own far edge, not the
window's.

| ref | scene | battery s0 ft | check | measured ft | e solver ft | e model ft |
|---|---|---|---|---|---|---|
| main | window-near | 0.00 | opening_clearance | 0.000 | 1.8133 | 1.8133 |
| main | window-near | 0.02 | opening_clearance | 0.000 | 1.8167 | 1.8167 |
| main | window-near | 0.04 | opening_clearance | 0.000 | 1.8200 | 1.8200 |
| main | window-near | 0.12 | opening_clearance | 0.000 | 1.8333 | 1.8333 |
| main | window-near | 0.21 | opening_clearance | 0.000 | 1.8467 | 1.8467 |
| main | window-near | 0.29 | opening_clearance | 0.000 | 1.8600 | 1.8600 |
| ... | window-near | ... | opening_clearance | ... (393 of 405 rows elided) | ... | ... |
| main | window-near | 27.29 | opening_clearance | 22.292 | 6.1800 | 6.1800 |
| main | window-near | 27.38 | opening_clearance | 22.375 | 6.1933 | 6.1933 |
| main | window-near | 27.46 | opening_clearance | 22.458 | 6.2067 | 6.2067 |
| main | window-near | 27.54 | opening_clearance | 22.542 | 6.2200 | 6.2200 |
| main | window-near | 27.59 | opening_clearance | 22.588 | 6.2275 | 6.2275 |
| main | window-near | 27.63 | opening_clearance | 22.635 | 6.2349 | 6.2349 |
| main | window-far | 0.00 | opening_clearance | 12.417 | 3.8933 | 3.8933 |
| main | window-far | 0.02 | opening_clearance | 12.396 | 3.8967 | 3.8967 |
| main | window-far | 0.04 | opening_clearance | 12.375 | 3.9000 | 3.9000 |
| main | window-far | 0.12 | opening_clearance | 12.292 | 3.9133 | 3.9133 |
| main | window-far | 0.21 | opening_clearance | 12.208 | 3.9267 | 3.9267 |
| main | window-far | 0.29 | opening_clearance | 12.125 | 3.9400 | 3.9400 |
| ... | window-far | ... | opening_clearance | ... (475 of 487 rows elided) | ... | ... |
| main | window-far | 32.29 | opening_clearance | 14.292 | 9.0600 | 9.0600 |
| main | window-far | 32.38 | opening_clearance | 14.375 | 9.0733 | 9.0733 |
| main | window-far | 32.46 | opening_clearance | 14.458 | 9.0867 | 9.0867 |
| main | window-far | 32.54 | opening_clearance | 14.542 | 9.1000 | 9.1000 |
| main | window-far | 32.56 | opening_clearance | 14.564 | 9.1037 | 9.1037 |
| main | window-far | 32.59 | opening_clearance | 14.587 | 9.1073 | 9.1073 |
| main | window-and-meter | 0.00 | gas_clearance | 17.417 | 4.5333 | 4.5333 |
| main | window-and-meter | 0.00 | opening_clearance | 12.417 | 3.8933 | 3.8933 |
| main | window-and-meter | 0.02 | gas_clearance | 17.396 | 4.5367 | 4.5367 |
| main | window-and-meter | 0.02 | opening_clearance | 12.396 | 3.8967 | 3.8967 |
| main | window-and-meter | 0.04 | gas_clearance | 17.375 | 4.5400 | 4.5400 |
| main | window-and-meter | 0.04 | opening_clearance | 12.375 | 3.9000 | 3.9000 |
| main | window-and-meter | 0.12 | gas_clearance | 17.292 | 4.5533 | 4.5533 |
| main | window-and-meter | 0.12 | opening_clearance | 12.292 | 3.9133 | 3.9133 |
| main | window-and-meter | 0.21 | gas_clearance | 17.208 | 4.5667 | 4.5667 |
| main | window-and-meter | 0.21 | opening_clearance | 12.208 | 3.9267 | 3.9267 |
| main | window-and-meter | 0.29 | gas_clearance | 17.125 | 4.5800 | 4.5800 |
| main | window-and-meter | 0.29 | opening_clearance | 12.125 | 3.9400 | 3.9400 |
| ... | window-and-meter | ... | gas_clearance | ... (599 of 611 rows elided) | ... | ... |
| ... | window-and-meter | ... | opening_clearance | ... (599 of 611 rows elided) | ... | ... |
| main | window-and-meter | 37.12 | gas_clearance | 15.125 | 10.4733 | 10.4733 |
| main | window-and-meter | 37.12 | opening_clearance | 19.125 | 9.8333 | 9.8333 |
| main | window-and-meter | 37.21 | gas_clearance | 15.208 | 10.4867 | 10.4867 |
| main | window-and-meter | 37.21 | opening_clearance | 19.208 | 9.8467 | 9.8467 |
| main | window-and-meter | 37.29 | gas_clearance | 15.292 | 10.5000 | 10.5000 |
| main | window-and-meter | 37.29 | opening_clearance | 19.292 | 9.8600 | 9.8600 |
| main | window-and-meter | 37.38 | gas_clearance | 15.375 | 10.5133 | 10.5133 |
| main | window-and-meter | 37.38 | opening_clearance | 19.375 | 9.8733 | 9.8733 |
| main | window-and-meter | 37.40 | gas_clearance | 15.396 | 10.5167 | 10.5167 |
| main | window-and-meter | 37.40 | opening_clearance | 19.396 | 9.8767 | 9.8767 |
| main | window-and-meter | 37.42 | gas_clearance | 15.417 | 10.5200 | 10.5200 |
| main | window-and-meter | 37.42 | opening_clearance | 19.417 | 9.8800 | 9.8800 |
| origin/t3/server | window-near | 0.00 | drive_clearance | 0.000 | 7.7133 | 7.7133 |
| origin/t3/server | window-near | 0.00 | opening_clearance | 0.000 | 1.8133 | 1.8133 |
| origin/t3/server | window-near | 0.02 | drive_clearance | 0.000 | 7.7167 | 7.7167 |
| origin/t3/server | window-near | 0.02 | opening_clearance | 0.000 | 1.8167 | 1.8167 |
| origin/t3/server | window-near | 0.04 | drive_clearance | 0.000 | 7.7200 | 7.7200 |
| origin/t3/server | window-near | 0.04 | opening_clearance | 0.000 | 1.8200 | 1.8200 |
| origin/t3/server | window-near | 0.10 | drive_clearance | 0.000 | 7.7295 | 7.7295 |
| origin/t3/server | window-near | 0.10 | opening_clearance | 0.000 | 1.8295 | 1.8295 |
| origin/t3/server | window-near | 0.16 | drive_clearance | 0.000 | 7.7391 | 7.7391 |
| origin/t3/server | window-near | 0.16 | opening_clearance | 0.000 | 1.8391 | 1.8391 |
| origin/t3/server | window-near | 0.18 | drive_clearance | 0.000 | 7.7425 | 7.7425 |
| origin/t3/server | window-near | 0.18 | opening_clearance | 0.000 | 1.8425 | 1.8425 |
| ... | window-near | ... | drive_clearance | ... (407 of 419 rows elided) | ... | ... |
| ... | window-near | ... | opening_clearance | ... (407 of 419 rows elided) | ... | ... |
| origin/t3/server | window-near | 26.96 | drive_clearance | 0.000 | 12.0267 | 12.0267 |
| origin/t3/server | window-near | 26.96 | opening_clearance | 21.958 | 6.1267 | 6.1267 |
| origin/t3/server | window-near | 27.04 | drive_clearance | 0.000 | 12.0400 | 12.0400 |
| origin/t3/server | window-near | 27.04 | opening_clearance | 22.042 | 6.1400 | 6.1400 |
| origin/t3/server | window-near | 27.12 | drive_clearance | 0.000 | 12.0533 | 12.0533 |
| origin/t3/server | window-near | 27.12 | opening_clearance | 22.125 | 6.1533 | 6.1533 |
| origin/t3/server | window-near | 27.21 | drive_clearance | 0.000 | 12.0667 | 12.0667 |
| origin/t3/server | window-near | 27.21 | opening_clearance | 22.208 | 6.1667 | 6.1667 |
| origin/t3/server | window-near | 27.24 | drive_clearance | 0.000 | 12.0722 | 12.0722 |
| origin/t3/server | window-near | 27.24 | opening_clearance | 22.243 | 6.1722 | 6.1722 |
| origin/t3/server | window-near | 27.28 | drive_clearance | 0.000 | 12.0778 | 12.0778 |
| origin/t3/server | window-near | 27.28 | opening_clearance | 22.278 | 6.1778 | 6.1778 |
| origin/t3/server | window-far | 0.00 | drive_clearance | 0.000 | 7.7133 | 7.7133 |
| origin/t3/server | window-far | 0.00 | opening_clearance | 12.417 | 3.8933 | 3.8933 |
| origin/t3/server | window-far | 0.02 | drive_clearance | 0.000 | 7.7167 | 7.7167 |
| origin/t3/server | window-far | 0.02 | opening_clearance | 12.396 | 3.8967 | 3.8967 |
| origin/t3/server | window-far | 0.04 | drive_clearance | 0.000 | 7.7200 | 7.7200 |
| origin/t3/server | window-far | 0.04 | opening_clearance | 12.375 | 3.9000 | 3.9000 |
| origin/t3/server | window-far | 0.12 | drive_clearance | 0.000 | 7.7330 | 7.7330 |
| origin/t3/server | window-far | 0.12 | opening_clearance | 12.294 | 3.9130 | 3.9130 |
| origin/t3/server | window-far | 0.20 | drive_clearance | 0.000 | 7.7460 | 7.7460 |
| origin/t3/server | window-far | 0.20 | opening_clearance | 12.213 | 3.9260 | 3.9260 |
| origin/t3/server | window-far | 0.21 | drive_clearance | 0.000 | 7.7463 | 7.7463 |
| origin/t3/server | window-far | 0.21 | opening_clearance | 12.210 | 3.9263 | 3.9263 |
| ... | window-far | ... | drive_clearance | ... (485 of 497 rows elided) | ... | ... |
| ... | window-far | ... | opening_clearance | ... (485 of 497 rows elided) | ... | ... |
| origin/t3/server | window-far | 31.96 | drive_clearance | 0.000 | 12.8267 | 12.8267 |
| origin/t3/server | window-far | 31.96 | opening_clearance | 13.958 | 9.0067 | 9.0067 |
| origin/t3/server | window-far | 32.04 | drive_clearance | 0.000 | 12.8400 | 12.8400 |
| origin/t3/server | window-far | 32.04 | opening_clearance | 14.042 | 9.0200 | 9.0200 |
| origin/t3/server | window-far | 32.12 | drive_clearance | 0.000 | 12.8533 | 12.8533 |
| origin/t3/server | window-far | 32.12 | opening_clearance | 14.125 | 9.0333 | 9.0333 |
| origin/t3/server | window-far | 32.21 | drive_clearance | 0.000 | 12.8667 | 12.8667 |
| origin/t3/server | window-far | 32.21 | opening_clearance | 14.208 | 9.0467 | 9.0467 |
| origin/t3/server | window-far | 32.22 | drive_clearance | 0.000 | 12.8684 | 12.8684 |
| origin/t3/server | window-far | 32.22 | opening_clearance | 14.219 | 9.0484 | 9.0484 |
| origin/t3/server | window-far | 32.23 | drive_clearance | 0.000 | 12.8702 | 12.8702 |
| origin/t3/server | window-far | 32.23 | opening_clearance | 14.230 | 9.0502 | 9.0502 |
| origin/t3/server | window-and-meter | 0.00 | gas_clearance | 17.417 | 4.5333 | 4.5333 |
| origin/t3/server | window-and-meter | 0.00 | drive_clearance | 0.000 | 7.7133 | 7.7133 |
| origin/t3/server | window-and-meter | 0.00 | opening_clearance | 12.417 | 3.8933 | 3.8933 |
| origin/t3/server | window-and-meter | 0.02 | gas_clearance | 17.396 | 4.5367 | 4.5367 |
| origin/t3/server | window-and-meter | 0.02 | drive_clearance | 0.000 | 7.7167 | 7.7167 |
| origin/t3/server | window-and-meter | 0.02 | opening_clearance | 12.396 | 3.8967 | 3.8967 |
| origin/t3/server | window-and-meter | 0.04 | gas_clearance | 17.375 | 4.5400 | 4.5400 |
| origin/t3/server | window-and-meter | 0.04 | drive_clearance | 0.000 | 7.7200 | 7.7200 |
| origin/t3/server | window-and-meter | 0.04 | opening_clearance | 12.375 | 3.9000 | 3.9000 |
| origin/t3/server | window-and-meter | 0.12 | gas_clearance | 17.294 | 4.5530 | 4.5530 |
| origin/t3/server | window-and-meter | 0.12 | drive_clearance | 0.000 | 7.7330 | 7.7330 |
| origin/t3/server | window-and-meter | 0.12 | opening_clearance | 12.294 | 3.9130 | 3.9130 |
| origin/t3/server | window-and-meter | 0.20 | gas_clearance | 17.213 | 4.5660 | 4.5660 |
| origin/t3/server | window-and-meter | 0.20 | drive_clearance | 0.000 | 7.7460 | 7.7460 |
| origin/t3/server | window-and-meter | 0.20 | opening_clearance | 12.213 | 3.9260 | 3.9260 |
| origin/t3/server | window-and-meter | 0.21 | gas_clearance | 17.210 | 4.5663 | 4.5663 |
| origin/t3/server | window-and-meter | 0.21 | drive_clearance | 0.000 | 7.7463 | 7.7463 |
| origin/t3/server | window-and-meter | 0.21 | opening_clearance | 12.210 | 3.9263 | 3.9263 |
| ... | window-and-meter | ... | gas_clearance | ... (619 of 631 rows elided) | ... | ... |
| ... | window-and-meter | ... | drive_clearance | ... (619 of 631 rows elided) | ... | ... |
| ... | window-and-meter | ... | opening_clearance | ... (619 of 631 rows elided) | ... | ... |
| origin/t3/server | window-and-meter | 37.12 | gas_clearance | 15.125 | 10.4733 | 10.4733 |
| origin/t3/server | window-and-meter | 37.12 | drive_clearance | 0.000 | 13.6533 | 13.6533 |
| origin/t3/server | window-and-meter | 37.12 | opening_clearance | 19.125 | 9.8333 | 9.8333 |
| origin/t3/server | window-and-meter | 37.21 | gas_clearance | 15.208 | 10.4867 | 10.4867 |
| origin/t3/server | window-and-meter | 37.21 | drive_clearance | 0.000 | 13.6667 | 13.6667 |
| origin/t3/server | window-and-meter | 37.21 | opening_clearance | 19.208 | 9.8467 | 9.8467 |
| origin/t3/server | window-and-meter | 37.29 | gas_clearance | 15.292 | 10.5000 | 10.5000 |
| origin/t3/server | window-and-meter | 37.29 | drive_clearance | 0.000 | 13.6800 | 13.6800 |
| origin/t3/server | window-and-meter | 37.29 | opening_clearance | 19.292 | 9.8600 | 9.8600 |
| origin/t3/server | window-and-meter | 37.38 | gas_clearance | 15.375 | 10.5133 | 10.5133 |
| origin/t3/server | window-and-meter | 37.38 | drive_clearance | 0.000 | 13.6933 | 13.6933 |
| origin/t3/server | window-and-meter | 37.38 | opening_clearance | 19.375 | 9.8733 | 9.8733 |
| origin/t3/server | window-and-meter | 37.40 | gas_clearance | 15.396 | 10.5167 | 10.5167 |
| origin/t3/server | window-and-meter | 37.40 | drive_clearance | 0.000 | 13.6967 | 13.6967 |
| origin/t3/server | window-and-meter | 37.40 | opening_clearance | 19.396 | 9.8767 | 9.8767 |
| origin/t3/server | window-and-meter | 37.42 | gas_clearance | 15.417 | 10.5200 | 10.5200 |
| origin/t3/server | window-and-meter | 37.42 | drive_clearance | 0.000 | 13.7000 | 13.7000 |
| origin/t3/server | window-and-meter | 37.42 | opening_clearance | 19.417 | 9.8800 | 9.8800 |
