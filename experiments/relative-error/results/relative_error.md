# Relative-error study: results

Decision accounting for five error bars on one-wall clearances, under error
fields whose marginals are calibrated to committed pooled p90s and whose
residual correlation is swept. [METHODS.md](METHODS.md) fixes the method;
[audit.md](results/audit.md) pins every input; [comparator.md](results/comparator.md)
pins the model of the shipped calculation to both solvers;
[sensitivity.md](results/sensitivity.md) moves one coefficient at a time.

## Bars at worked examples

All bars in feet. Both endpoints tapped; the last column swaps the battery
end for a photo-detected one.

### modern

| case | current | rate_only | rate_on_gap | scale_error | local_clearance | current (vlm feature) |
|---|---|---|---|---|---|---|
| battery 20-23 ft, window 3 ft at 23 ft, gap 3 | 7.48 | 6.88 | 1.08 | 0.644 | 0.182 | 8.68 |
| battery 15-18 ft, window 3 ft at 18 ft, gap 3 | 5.88 | 5.28 | 1.08 | 0.644 | 0.182 | 7.08 |
| battery edge at 18 ft, window 3 ft at 18 ft, gap 0 | 6.36 | 5.76 | 0.6 | 0.6 | 0.0 | 7.56 |

### phone_2018

| case | current | rate_only | rate_on_gap | scale_error | local_clearance | current (vlm feature) |
|---|---|---|---|---|---|---|
| battery 20-23 ft, window 3 ft at 23 ft, gap 3 | 7.48 | 6.88 | 1.08 | 0.704 | 0.182 | 8.68 |
| battery 15-18 ft, window 3 ft at 18 ft, gap 3 | 5.88 | 5.28 | 1.08 | 0.704 | 0.182 | 7.08 |
| battery edge at 18 ft, window 3 ft at 18 ft, gap 0 | 6.36 | 5.76 | 0.6 | 0.6 | 0.0 | 7.56 |

## Wrongly clears a violated 3 ft rule (true gap 2.9 ft, window at 20 ft)

| model | class | regime | residual corr length ft | rate |
|---|---|---|---|---|
| current | modern | same_walk | 2.0 | 0.00% |
| current | modern | same_walk | 5.0 | 0.00% |
| current | modern | same_walk | 10.0 | 0.00% |
| current | modern | same_walk | 20.0 | 0.00% |
| current | modern | same_walk | inf | 0.00% |
| current | modern | cross_session | 2.0 | 0.00% |
| current | modern | cross_session | 5.0 | 0.00% |
| current | modern | cross_session | 10.0 | 0.00% |
| current | modern | cross_session | 20.0 | 0.00% |
| current | modern | cross_session | inf | 0.00% |
| current | phone_2018 | same_walk | 2.0 | 18.22% |
| current | phone_2018 | same_walk | 5.0 | 9.50% |
| current | phone_2018 | same_walk | 10.0 | 3.67% |
| current | phone_2018 | same_walk | 20.0 | 0.75% |
| current | phone_2018 | same_walk | inf | 0.00% |
| current | phone_2018 | cross_session | 2.0 | 24.15% |
| current | phone_2018 | cross_session | 5.0 | 24.25% |
| current | phone_2018 | cross_session | 10.0 | 24.01% |
| current | phone_2018 | cross_session | 20.0 | 24.10% |
| current | phone_2018 | cross_session | inf | 24.30% |
| rate_only | modern | same_walk | 2.0 | 0.00% |
| rate_only | modern | same_walk | 5.0 | 0.00% |
| rate_only | modern | same_walk | 10.0 | 0.00% |
| rate_only | modern | same_walk | 20.0 | 0.00% |
| rate_only | modern | same_walk | inf | 0.00% |
| rate_only | modern | cross_session | 2.0 | 0.00% |
| rate_only | modern | cross_session | 5.0 | 0.00% |
| rate_only | modern | cross_session | 10.0 | 0.00% |
| rate_only | modern | cross_session | 20.0 | 0.00% |
| rate_only | modern | cross_session | inf | 0.00% |
| rate_only | phone_2018 | same_walk | 2.0 | 20.69% |
| rate_only | phone_2018 | same_walk | 5.0 | 11.63% |
| rate_only | phone_2018 | same_walk | 10.0 | 4.88% |
| rate_only | phone_2018 | same_walk | 20.0 | 1.21% |
| rate_only | phone_2018 | same_walk | inf | 0.00% |
| rate_only | phone_2018 | cross_session | 2.0 | 26.99% |
| rate_only | phone_2018 | cross_session | 5.0 | 27.03% |
| rate_only | phone_2018 | cross_session | 10.0 | 26.88% |
| rate_only | phone_2018 | cross_session | 20.0 | 26.69% |
| rate_only | phone_2018 | cross_session | inf | 26.77% |
| rate_on_gap | modern | same_walk | 2.0 | 14.29% |
| rate_on_gap | modern | same_walk | 5.0 | 8.17% |
| rate_on_gap | modern | same_walk | 10.0 | 3.29% |
| rate_on_gap | modern | same_walk | 20.0 | 0.74% |
| rate_on_gap | modern | same_walk | inf | 0.00% |
| rate_on_gap | modern | cross_session | 2.0 | 18.32% |
| rate_on_gap | modern | cross_session | 5.0 | 18.71% |
| rate_on_gap | modern | cross_session | 10.0 | 18.72% |
| rate_on_gap | modern | cross_session | 20.0 | 19.05% |
| rate_on_gap | modern | cross_session | inf | 19.11% |
| rate_on_gap | phone_2018 | same_walk | 2.0 | 60.48% |
| rate_on_gap | phone_2018 | same_walk | 5.0 | 51.59% |
| rate_on_gap | phone_2018 | same_walk | 10.0 | 42.80% |
| rate_on_gap | phone_2018 | same_walk | 20.0 | 33.86% |
| rate_on_gap | phone_2018 | same_walk | inf | 3.73% |
| rate_on_gap | phone_2018 | cross_session | 2.0 | 65.16% |
| rate_on_gap | phone_2018 | cross_session | 5.0 | 65.53% |
| rate_on_gap | phone_2018 | cross_session | 10.0 | 65.06% |
| rate_on_gap | phone_2018 | cross_session | 20.0 | 64.56% |
| rate_on_gap | phone_2018 | cross_session | inf | 64.60% |
| scale_error | modern | same_walk | 2.0 | 24.75% |
| scale_error | modern | same_walk | 5.0 | 18.79% |
| scale_error | modern | same_walk | 10.0 | 11.97% |
| scale_error | modern | same_walk | 20.0 | 5.79% |
| scale_error | modern | same_walk | inf | 0.00% |
| scale_error | modern | cross_session | 2.0 | 28.35% |
| scale_error | modern | cross_session | 5.0 | 28.63% |
| scale_error | modern | cross_session | 10.0 | 28.50% |
| scale_error | modern | cross_session | 20.0 | 28.80% |
| scale_error | modern | cross_session | inf | 28.73% |
| scale_error | phone_2018 | same_walk | 2.0 | 63.71% |
| scale_error | phone_2018 | same_walk | 5.0 | 55.46% |
| scale_error | phone_2018 | same_walk | 10.0 | 47.17% |
| scale_error | phone_2018 | same_walk | 20.0 | 38.66% |
| scale_error | phone_2018 | same_walk | inf | 8.66% |
| scale_error | phone_2018 | cross_session | 2.0 | 68.25% |
| scale_error | phone_2018 | cross_session | 5.0 | 68.25% |
| scale_error | phone_2018 | cross_session | 10.0 | 68.18% |
| scale_error | phone_2018 | cross_session | 20.0 | 67.66% |
| scale_error | phone_2018 | cross_session | inf | 67.48% |
| local_clearance | modern | same_walk | 2.0 | 39.77% |
| local_clearance | modern | same_walk | 5.0 | 37.13% |
| local_clearance | modern | same_walk | 10.0 | 33.22% |
| local_clearance | modern | same_walk | 20.0 | 28.17% |
| local_clearance | modern | same_walk | inf | 1.70% |
| local_clearance | modern | cross_session | 2.0 | 41.49% |
| local_clearance | modern | cross_session | 5.0 | 41.38% |
| local_clearance | modern | cross_session | 10.0 | 41.27% |
| local_clearance | modern | cross_session | 20.0 | 41.82% |
| local_clearance | modern | cross_session | inf | 41.66% |
| local_clearance | phone_2018 | same_walk | 2.0 | 68.75% |
| local_clearance | phone_2018 | same_walk | 5.0 | 61.17% |
| local_clearance | phone_2018 | same_walk | 10.0 | 53.51% |
| local_clearance | phone_2018 | same_walk | 20.0 | 45.85% |
| local_clearance | phone_2018 | same_walk | inf | 23.60% |
| local_clearance | phone_2018 | cross_session | 2.0 | 72.54% |
| local_clearance | phone_2018 | cross_session | 5.0 | 72.45% |
| local_clearance | phone_2018 | cross_session | 10.0 | 72.45% |
| local_clearance | phone_2018 | cross_session | 20.0 | 71.93% |
| local_clearance | phone_2018 | cross_session | inf | 71.88% |
| common_mode_oracle | modern | same_walk | 2.0 | 44.96% |
| common_mode_oracle | modern | same_walk | 5.0 | 43.57% |
| common_mode_oracle | modern | same_walk | 10.0 | 41.39% |
| common_mode_oracle | modern | same_walk | 20.0 | 38.93% |
| common_mode_oracle | modern | same_walk | inf | 14.38% |
| common_mode_oracle | phone_2018 | same_walk | 2.0 | 67.42% |
| common_mode_oracle | phone_2018 | same_walk | 5.0 | 59.56% |
| common_mode_oracle | phone_2018 | same_walk | 10.0 | 51.71% |
| common_mode_oracle | phone_2018 | same_walk | 20.0 | 43.82% |
| common_mode_oracle | phone_2018 | same_walk | inf | 18.88% |

## UNSURE although the rule is met (true gap 3.3 ft, window at 20 ft)

| model | class | regime | residual corr length ft | rate |
|---|---|---|---|---|
| current | modern | same_walk | 2.0 | 100.00% |
| current | modern | same_walk | 5.0 | 100.00% |
| current | modern | same_walk | 10.0 | 100.00% |
| current | modern | same_walk | 20.0 | 100.00% |
| current | modern | same_walk | inf | 100.00% |
| current | modern | cross_session | 2.0 | 100.00% |
| current | modern | cross_session | 5.0 | 100.00% |
| current | modern | cross_session | 10.0 | 100.00% |
| current | modern | cross_session | 20.0 | 100.00% |
| current | modern | cross_session | inf | 100.00% |
| current | phone_2018 | same_walk | 2.0 | 79.37% |
| current | phone_2018 | same_walk | 5.0 | 88.42% |
| current | phone_2018 | same_walk | 10.0 | 94.93% |
| current | phone_2018 | same_walk | 20.0 | 98.49% |
| current | phone_2018 | same_walk | inf | 100.00% |
| current | phone_2018 | cross_session | 2.0 | 74.56% |
| current | phone_2018 | cross_session | 5.0 | 75.48% |
| current | phone_2018 | cross_session | 10.0 | 74.76% |
| current | phone_2018 | cross_session | 20.0 | 75.22% |
| current | phone_2018 | cross_session | inf | 74.88% |
| rate_only | modern | same_walk | 2.0 | 100.00% |
| rate_only | modern | same_walk | 5.0 | 100.00% |
| rate_only | modern | same_walk | 10.0 | 100.00% |
| rate_only | modern | same_walk | 20.0 | 100.00% |
| rate_only | modern | same_walk | inf | 100.00% |
| rate_only | modern | cross_session | 2.0 | 100.00% |
| rate_only | modern | cross_session | 5.0 | 100.00% |
| rate_only | modern | cross_session | 10.0 | 100.00% |
| rate_only | modern | cross_session | 20.0 | 100.00% |
| rate_only | modern | cross_session | inf | 100.00% |
| rate_only | phone_2018 | same_walk | 2.0 | 76.63% |
| rate_only | phone_2018 | same_walk | 5.0 | 86.22% |
| rate_only | phone_2018 | same_walk | 10.0 | 93.39% |
| rate_only | phone_2018 | same_walk | 20.0 | 97.68% |
| rate_only | phone_2018 | same_walk | inf | 100.00% |
| rate_only | phone_2018 | cross_session | 2.0 | 71.76% |
| rate_only | phone_2018 | cross_session | 5.0 | 72.78% |
| rate_only | phone_2018 | cross_session | 10.0 | 72.11% |
| rate_only | phone_2018 | cross_session | 20.0 | 72.41% |
| rate_only | phone_2018 | cross_session | inf | 71.91% |
| rate_on_gap | modern | same_walk | 2.0 | 66.49% |
| rate_on_gap | modern | same_walk | 5.0 | 77.35% |
| rate_on_gap | modern | same_walk | 10.0 | 87.19% |
| rate_on_gap | modern | same_walk | 20.0 | 94.73% |
| rate_on_gap | modern | same_walk | inf | 100.00% |
| rate_on_gap | modern | cross_session | 2.0 | 59.04% |
| rate_on_gap | modern | cross_session | 5.0 | 58.57% |
| rate_on_gap | modern | cross_session | 10.0 | 58.86% |
| rate_on_gap | modern | cross_session | 20.0 | 58.96% |
| rate_on_gap | modern | cross_session | inf | 58.86% |
| rate_on_gap | phone_2018 | same_walk | 2.0 | 20.66% |
| rate_on_gap | phone_2018 | same_walk | 5.0 | 24.01% |
| rate_on_gap | phone_2018 | same_walk | 10.0 | 27.22% |
| rate_on_gap | phone_2018 | same_walk | 20.0 | 31.66% |
| rate_on_gap | phone_2018 | same_walk | inf | 77.52% |
| rate_on_gap | phone_2018 | cross_session | 2.0 | 18.94% |
| rate_on_gap | phone_2018 | cross_session | 5.0 | 18.94% |
| rate_on_gap | phone_2018 | cross_session | 10.0 | 18.54% |
| rate_on_gap | phone_2018 | cross_session | 20.0 | 18.62% |
| rate_on_gap | phone_2018 | cross_session | inf | 18.73% |
| scale_error | modern | same_walk | 2.0 | 41.91% |
| scale_error | modern | same_walk | 5.0 | 51.68% |
| scale_error | modern | same_walk | 10.0 | 61.27% |
| scale_error | modern | same_walk | 20.0 | 72.45% |
| scale_error | modern | same_walk | inf | 99.19% |
| scale_error | modern | cross_session | 2.0 | 36.63% |
| scale_error | modern | cross_session | 5.0 | 36.05% |
| scale_error | modern | cross_session | 10.0 | 36.52% |
| scale_error | modern | cross_session | 20.0 | 36.45% |
| scale_error | modern | cross_session | inf | 36.36% |
| scale_error | phone_2018 | same_walk | 2.0 | 13.21% |
| scale_error | phone_2018 | same_walk | 5.0 | 15.20% |
| scale_error | phone_2018 | same_walk | 10.0 | 17.32% |
| scale_error | phone_2018 | same_walk | 20.0 | 20.16% |
| scale_error | phone_2018 | same_walk | inf | 55.81% |
| scale_error | phone_2018 | cross_session | 2.0 | 11.96% |
| scale_error | phone_2018 | cross_session | 5.0 | 12.04% |
| scale_error | phone_2018 | cross_session | 10.0 | 11.86% |
| scale_error | phone_2018 | cross_session | 20.0 | 11.87% |
| scale_error | phone_2018 | cross_session | inf | 12.03% |
| local_clearance | modern | same_walk | 2.0 | 13.64% |
| local_clearance | modern | same_walk | 5.0 | 16.86% |
| local_clearance | modern | same_walk | 10.0 | 20.73% |
| local_clearance | modern | same_walk | 20.0 | 26.04% |
| local_clearance | modern | same_walk | inf | 24.28% |
| local_clearance | modern | cross_session | 2.0 | 11.70% |
| local_clearance | modern | cross_session | 5.0 | 11.40% |
| local_clearance | modern | cross_session | 10.0 | 11.63% |
| local_clearance | modern | cross_session | 20.0 | 11.41% |
| local_clearance | modern | cross_session | inf | 11.68% |
| local_clearance | phone_2018 | same_walk | 2.0 | 3.80% |
| local_clearance | phone_2018 | same_walk | 5.0 | 4.42% |
| local_clearance | phone_2018 | same_walk | 10.0 | 5.00% |
| local_clearance | phone_2018 | same_walk | 20.0 | 5.75% |
| local_clearance | phone_2018 | same_walk | inf | 17.13% |
| local_clearance | phone_2018 | cross_session | 2.0 | 3.29% |
| local_clearance | phone_2018 | cross_session | 5.0 | 3.40% |
| local_clearance | phone_2018 | cross_session | 10.0 | 3.48% |
| local_clearance | phone_2018 | cross_session | 20.0 | 3.31% |
| local_clearance | phone_2018 | cross_session | inf | 3.26% |

## Coverage: bar >= true difference error (true gap 3.0 ft, window at 20 ft)

| model | class | regime | residual corr length ft | rate |
|---|---|---|---|---|
| current | modern | same_walk | 2.0 | 100.00% |
| current | modern | same_walk | 5.0 | 100.00% |
| current | modern | same_walk | 10.0 | 100.00% |
| current | modern | same_walk | 20.0 | 100.00% |
| current | modern | same_walk | inf | 100.00% |
| current | modern | cross_session | 2.0 | 100.00% |
| current | modern | cross_session | 5.0 | 100.00% |
| current | modern | cross_session | 10.0 | 100.00% |
| current | modern | cross_session | 20.0 | 100.00% |
| current | modern | cross_session | inf | 100.00% |
| current | phone_2018 | same_walk | 2.0 | 80.69% |
| current | phone_2018 | same_walk | 5.0 | 90.26% |
| current | phone_2018 | same_walk | 10.0 | 95.95% |
| current | phone_2018 | same_walk | 20.0 | 99.13% |
| current | phone_2018 | same_walk | inf | 100.00% |
| current | phone_2018 | cross_session | 2.0 | 75.19% |
| current | phone_2018 | cross_session | 5.0 | 75.88% |
| current | phone_2018 | cross_session | 10.0 | 75.90% |
| current | phone_2018 | cross_session | 20.0 | 75.02% |
| current | phone_2018 | cross_session | inf | 76.40% |
| rate_only | modern | same_walk | 2.0 | 100.00% |
| rate_only | modern | same_walk | 5.0 | 100.00% |
| rate_only | modern | same_walk | 10.0 | 100.00% |
| rate_only | modern | same_walk | 20.0 | 100.00% |
| rate_only | modern | same_walk | inf | 100.00% |
| rate_only | modern | cross_session | 2.0 | 100.00% |
| rate_only | modern | cross_session | 5.0 | 100.00% |
| rate_only | modern | cross_session | 10.0 | 100.00% |
| rate_only | modern | cross_session | 20.0 | 100.00% |
| rate_only | modern | cross_session | inf | 100.00% |
| rate_only | phone_2018 | same_walk | 2.0 | 78.23% |
| rate_only | phone_2018 | same_walk | 5.0 | 88.28% |
| rate_only | phone_2018 | same_walk | 10.0 | 94.51% |
| rate_only | phone_2018 | same_walk | 20.0 | 98.63% |
| rate_only | phone_2018 | same_walk | inf | 100.00% |
| rate_only | phone_2018 | cross_session | 2.0 | 72.42% |
| rate_only | phone_2018 | cross_session | 5.0 | 73.12% |
| rate_only | phone_2018 | cross_session | 10.0 | 73.06% |
| rate_only | phone_2018 | cross_session | 20.0 | 72.15% |
| rate_only | phone_2018 | cross_session | inf | 73.67% |
| rate_on_gap | modern | same_walk | 2.0 | 67.05% |
| rate_on_gap | modern | same_walk | 5.0 | 79.72% |
| rate_on_gap | modern | same_walk | 10.0 | 90.53% |
| rate_on_gap | modern | same_walk | 20.0 | 97.34% |
| rate_on_gap | modern | same_walk | inf | 100.00% |
| rate_on_gap | modern | cross_session | 2.0 | 58.43% |
| rate_on_gap | modern | cross_session | 5.0 | 58.32% |
| rate_on_gap | modern | cross_session | 10.0 | 58.43% |
| rate_on_gap | modern | cross_session | 20.0 | 58.48% |
| rate_on_gap | modern | cross_session | inf | 58.62% |
| rate_on_gap | phone_2018 | same_walk | 2.0 | 19.56% |
| rate_on_gap | phone_2018 | same_walk | 5.0 | 23.82% |
| rate_on_gap | phone_2018 | same_walk | 10.0 | 27.57% |
| rate_on_gap | phone_2018 | same_walk | 20.0 | 31.72% |
| rate_on_gap | phone_2018 | same_walk | inf | 76.28% |
| rate_on_gap | phone_2018 | cross_session | 2.0 | 17.81% |
| rate_on_gap | phone_2018 | cross_session | 5.0 | 18.35% |
| rate_on_gap | phone_2018 | cross_session | 10.0 | 18.33% |
| rate_on_gap | phone_2018 | cross_session | 20.0 | 17.75% |
| rate_on_gap | phone_2018 | cross_session | inf | 17.77% |
| scale_error | modern | same_walk | 2.0 | 43.85% |
| scale_error | modern | same_walk | 5.0 | 55.30% |
| scale_error | modern | same_walk | 10.0 | 68.11% |
| scale_error | modern | same_walk | 20.0 | 81.48% |
| scale_error | modern | same_walk | inf | 100.00% |
| scale_error | modern | cross_session | 2.0 | 37.42% |
| scale_error | modern | cross_session | 5.0 | 37.27% |
| scale_error | modern | cross_session | 10.0 | 37.65% |
| scale_error | modern | cross_session | 20.0 | 37.49% |
| scale_error | modern | cross_session | inf | 37.57% |
| scale_error | phone_2018 | same_walk | 2.0 | 13.08% |
| scale_error | phone_2018 | same_walk | 5.0 | 15.43% |
| scale_error | phone_2018 | same_walk | 10.0 | 18.01% |
| scale_error | phone_2018 | same_walk | 20.0 | 20.76% |
| scale_error | phone_2018 | same_walk | inf | 55.83% |
| scale_error | phone_2018 | cross_session | 2.0 | 11.28% |
| scale_error | phone_2018 | cross_session | 5.0 | 12.15% |
| scale_error | phone_2018 | cross_session | 10.0 | 12.10% |
| scale_error | phone_2018 | cross_session | 20.0 | 11.51% |
| scale_error | phone_2018 | cross_session | inf | 11.82% |
| local_clearance | modern | same_walk | 2.0 | 13.19% |
| local_clearance | modern | same_walk | 5.0 | 17.06% |
| local_clearance | modern | same_walk | 10.0 | 22.31% |
| local_clearance | modern | same_walk | 20.0 | 29.20% |
| local_clearance | modern | same_walk | inf | 83.78% |
| local_clearance | modern | cross_session | 2.0 | 11.19% |
| local_clearance | modern | cross_session | 5.0 | 10.79% |
| local_clearance | modern | cross_session | 10.0 | 11.22% |
| local_clearance | modern | cross_session | 20.0 | 10.81% |
| local_clearance | modern | cross_session | inf | 11.12% |
| local_clearance | phone_2018 | same_walk | 2.0 | 3.54% |
| local_clearance | phone_2018 | same_walk | 5.0 | 3.93% |
| local_clearance | phone_2018 | same_walk | 10.0 | 4.84% |
| local_clearance | phone_2018 | same_walk | 20.0 | 5.44% |
| local_clearance | phone_2018 | same_walk | inf | 15.42% |
| local_clearance | phone_2018 | cross_session | 2.0 | 2.83% |
| local_clearance | phone_2018 | cross_session | 5.0 | 2.99% |
| local_clearance | phone_2018 | cross_session | 10.0 | 3.03% |
| local_clearance | phone_2018 | cross_session | 20.0 | 2.84% |
| local_clearance | phone_2018 | cross_session | inf | 3.26% |
| common_mode_oracle | modern | same_walk | 2.0 | 2.56% |
| common_mode_oracle | modern | same_walk | 5.0 | 3.25% |
| common_mode_oracle | modern | same_walk | 10.0 | 4.38% |
| common_mode_oracle | modern | same_walk | 20.0 | 5.67% |
| common_mode_oracle | modern | same_walk | inf | 20.24% |
| common_mode_oracle | phone_2018 | same_walk | 2.0 | 6.30% |
| common_mode_oracle | phone_2018 | same_walk | 5.0 | 7.46% |
| common_mode_oracle | phone_2018 | same_walk | 10.0 | 8.69% |
| common_mode_oracle | phone_2018 | same_walk | 20.0 | 9.87% |
| common_mode_oracle | phone_2018 | same_walk | inf | 27.37% |

## Calibration

Simulated absolute-position p90 against the committed pooled p90 at the fit
distances, with the residual spread set to the largest implied value.

| class | walked ft | implied k per ft | published p90 in | simulated p90 in |
|---|---|---|---|---|
| modern | 10 | 0.0410 | 8.6 | 8.6 |
| modern | 20 | 0.0306 | 13.4 | 17.15 |
| modern | 30 | 0.0276 | 18.5 | 25.78 |
| phone_2018 | 10 | 0.2800 | 55.7 | 59.88 |
| phone_2018 | 20 | 0.2371 | 94.61 | 119.5 |
| phone_2018 | 30 | 0.2212 | 132.58 | 179.32 |

## Comparator

- model reproduces both shipped solvers: True
- main and t3/server agree on every bar present in both refs: True
- main `c813182a808c957ccc981fa56490c6c29d3c1560`
- t3/server `1d1e7e1b55237ae82f5af10125ca33818f15cfce`
- 5839 clearance checks asserted at 1e-9
