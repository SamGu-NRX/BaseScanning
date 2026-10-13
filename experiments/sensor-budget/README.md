# Sensor budget

The tracked budget tables predate the conversion of empirical yaw quantiles to Gaussian sigmas. Their anchor-plane estimates and verdict need a new budget run before use.

## Question

What error does physics allow each signal we can't test on data, at the range it would be used? The signals are dual-camera disparity, focus distance, the barometer, UWB, known-size objects, one photo per span, and the phone as a probe. Each gets a formula and sourced optimistic and conservative parameters; [METHODS.md](METHODS.md) and [results/budget.md](results/budget.md) list them.

## Pass criteria, fixed before the run (397507f)

- **Worth a device test:** with conservative parameters, p90 error at the operating range is at most 2 in for a position or length, or at most 1% for a scale.
- **Drop:** even optimistic parameters exceed 4 in, or 3% for a scale.
- **In between:** listed with the parameter that decides it.

## Result

- **Drop:** dual-camera disparity, focus distance, the barometer, and a meter cover whose maker is unknown.
- **Worth a device test:** an ID card or Letter sheet at the meter, one photo per span with LiDAR (0.5 to 1.3 in over 6 ft), and a contact probe.
- **In between:** UWB needs σ ≤ 3 cm. The app's one-normal wall plane gives 3.1 to 9.8 in for an edge 6 ft away seen 35° off face-on.

**Run:** `uv run python budget.py`. It needs no data.

**What it changes:** there are no new sensors to build. The candidates are a card at the meter, a wider photo, and fixing the wall plane.
