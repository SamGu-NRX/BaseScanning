# Sensitivity of the headline rates to one coefficient at a time

Taps at 20 and 23 ft; violated gap 2.9 ft,
met gap 3.3 ft; residual correlation length 10 ft (same-walk regime).
Each row moves one parameter and re-decides the frozen rule at 3 ft.
Frozen values: drift 0.16 ft/ft, tap base 0.3 ft, class scale SD, calibrated
residual. An assumed correlation is not measured calibration: the residual
levels are assumptions, like the correlation length itself.

## Wrongly clears the violated 2.9 ft gap (rate)

| parameter | level | class | regime | current | rate_only | rate_on_gap | scale_error | local_clearance |
|---|---|---|---|---|---|---|---|---|
| drift_per_ft | 0.12 | modern | same_walk | 0.000  | 0.000  | 0.072  | 0.161  | 0.396 |
| drift_per_ft | 0.16 | modern | same_walk | 0.000  | 0.000  | 0.050  | 0.161  | 0.396 |
| drift_per_ft | 0.2 | modern | same_walk | 0.000  | 0.000  | 0.034  | 0.161  | 0.396 |
| tap_base_ft | 0.15 | modern | same_walk | 0.000  | 0.000  | 0.119  | 0.297  | 0.396 |
| tap_base_ft | 0.3 | modern | same_walk | 0.000  | 0.000  | 0.050  | 0.161  | 0.396 |
| tap_base_ft | 0.6 | modern | same_walk | 0.000  | 0.000  | 0.005  | 0.028  | 0.396 |
| scale_sd_x | 0.5 | modern | same_walk | 0.000  | 0.000  | 0.050  | 0.168  | 0.396 |
| scale_sd_x | 1.0 | modern | same_walk | 0.000  | 0.000  | 0.050  | 0.161  | 0.396 |
| scale_sd_x | 2.0 | modern | same_walk | 0.000  | 0.000  | 0.050  | 0.145  | 0.396 |
| residual_k_x | 0.5 | modern | same_walk | 0.000  | 0.000  | 0.000  | 0.024  | 0.293 |
| residual_k_x | 1.0 | modern | same_walk | 0.000  | 0.000  | 0.050  | 0.161  | 0.396 |
| residual_k_x | 1.5 | modern | same_walk | 0.000  | 0.000  | 0.136  | 0.253  | 0.432 |
| drift_per_ft | 0.12 | modern | cross_session | 0.000  | 0.000  | 0.247  | 0.323  | 0.451 |
| drift_per_ft | 0.16 | modern | cross_session | 0.000  | 0.000  | 0.221  | 0.323  | 0.451 |
| drift_per_ft | 0.2 | modern | cross_session | 0.000  | 0.000  | 0.194  | 0.323  | 0.451 |
| tap_base_ft | 0.15 | modern | cross_session | 0.000  | 0.000  | 0.292  | 0.405  | 0.451 |
| tap_base_ft | 0.3 | modern | cross_session | 0.000  | 0.000  | 0.221  | 0.323  | 0.451 |
| tap_base_ft | 0.6 | modern | cross_session | 0.000  | 0.000  | 0.111  | 0.182  | 0.451 |
| scale_sd_x | 0.5 | modern | cross_session | 0.000  | 0.000  | 0.221  | 0.329  | 0.451 |
| scale_sd_x | 1.0 | modern | cross_session | 0.000  | 0.000  | 0.221  | 0.323  | 0.451 |
| scale_sd_x | 2.0 | modern | cross_session | 0.000  | 0.000  | 0.221  | 0.311  | 0.451 |
| residual_k_x | 0.5 | modern | cross_session | 0.000  | 0.000  | 0.087  | 0.209  | 0.417 |
| residual_k_x | 1.0 | modern | cross_session | 0.000  | 0.000  | 0.221  | 0.323  | 0.451 |
| residual_k_x | 1.5 | modern | cross_session | 0.000  | 0.000  | 0.299  | 0.377  | 0.469 |
| drift_per_ft | 0.12 | phone_2018 | same_walk | 0.090  | 0.115  | 0.451  | 0.482  | 0.547 |
| drift_per_ft | 0.16 | phone_2018 | same_walk | 0.038  | 0.053  | 0.439  | 0.482  | 0.547 |
| drift_per_ft | 0.2 | phone_2018 | same_walk | 0.015  | 0.022  | 0.425  | 0.482  | 0.547 |
| tap_base_ft | 0.15 | phone_2018 | same_walk | 0.045  | 0.053  | 0.474  | 0.519  | 0.547 |
| tap_base_ft | 0.3 | phone_2018 | same_walk | 0.038  | 0.053  | 0.439  | 0.482  | 0.547 |
| tap_base_ft | 0.6 | phone_2018 | same_walk | 0.029  | 0.053  | 0.373  | 0.412  | 0.547 |
| scale_sd_x | 0.5 | phone_2018 | same_walk | 0.038  | 0.053  | 0.439  | 0.488  | 0.547 |
| scale_sd_x | 1.0 | phone_2018 | same_walk | 0.038  | 0.053  | 0.439  | 0.482  | 0.547 |
| scale_sd_x | 2.0 | phone_2018 | same_walk | 0.038  | 0.053  | 0.439  | 0.469  | 0.547 |
| residual_k_x | 0.5 | phone_2018 | same_walk | 0.000  | 0.001  | 0.267  | 0.327  | 0.416 |
| residual_k_x | 1.0 | phone_2018 | same_walk | 0.038  | 0.053  | 0.439  | 0.482  | 0.547 |
| residual_k_x | 1.5 | phone_2018 | same_walk | 0.141  | 0.167  | 0.571  | 0.604  | 0.657 |
| drift_per_ft | 0.12 | phone_2018 | cross_session | 0.328  | 0.363  | 0.661  | 0.681  | 0.723 |
| drift_per_ft | 0.16 | phone_2018 | cross_session | 0.240  | 0.271  | 0.651  | 0.681  | 0.723 |
| drift_per_ft | 0.2 | phone_2018 | cross_session | 0.171  | 0.194  | 0.642  | 0.681  | 0.723 |
| tap_base_ft | 0.15 | phone_2018 | cross_session | 0.255  | 0.271  | 0.676  | 0.705  | 0.723 |
| tap_base_ft | 0.3 | phone_2018 | cross_session | 0.240  | 0.271  | 0.651  | 0.681  | 0.723 |
| tap_base_ft | 0.6 | phone_2018 | cross_session | 0.214  | 0.271  | 0.605  | 0.633  | 0.723 |
| scale_sd_x | 0.5 | phone_2018 | cross_session | 0.240  | 0.271  | 0.651  | 0.686  | 0.723 |
| scale_sd_x | 1.0 | phone_2018 | cross_session | 0.240  | 0.271  | 0.651  | 0.681  | 0.723 |
| scale_sd_x | 2.0 | phone_2018 | cross_session | 0.240  | 0.271  | 0.651  | 0.673  | 0.723 |
| residual_k_x | 0.5 | phone_2018 | cross_session | 0.037  | 0.049  | 0.434  | 0.475  | 0.542 |
| residual_k_x | 1.0 | phone_2018 | cross_session | 0.240  | 0.271  | 0.651  | 0.681  | 0.723 |
| residual_k_x | 1.5 | phone_2018 | cross_session | 0.423  | 0.452  | 0.756  | 0.777  | 0.808 |

## UNSURE although the 3.3 ft gap is met (rate)

| parameter | level | class | regime | current | rate_only | rate_on_gap | scale_error | local_clearance |
|---|---|---|---|---|---|---|---|---|
| drift_per_ft | 0.12 | modern | same_walk | 1.000  | 1.000  | 0.875  | 0.686  | 0.248 |
| drift_per_ft | 0.16 | modern | same_walk | 1.000  | 1.000  | 0.919  | 0.686  | 0.248 |
| drift_per_ft | 0.2 | modern | same_walk | 1.000  | 1.000  | 0.950  | 0.686  | 0.248 |
| tap_base_ft | 0.15 | modern | same_walk | 1.000  | 1.000  | 0.800  | 0.412  | 0.248 |
| tap_base_ft | 0.3 | modern | same_walk | 1.000  | 1.000  | 0.919  | 0.686  | 0.248 |
| tap_base_ft | 0.6 | modern | same_walk | 1.000  | 1.000  | 0.992  | 0.948  | 0.248 |
| scale_sd_x | 0.5 | modern | same_walk | 1.000  | 1.000  | 0.919  | 0.668  | 0.248 |
| scale_sd_x | 1.0 | modern | same_walk | 1.000  | 1.000  | 0.919  | 0.686  | 0.248 |
| scale_sd_x | 2.0 | modern | same_walk | 1.000  | 1.000  | 0.919  | 0.723  | 0.248 |
| residual_k_x | 0.5 | modern | same_walk | 1.000  | 1.000  | 1.000  | 0.954  | 0.464 |
| residual_k_x | 1.0 | modern | same_walk | 1.000  | 1.000  | 0.919  | 0.686  | 0.248 |
| residual_k_x | 1.5 | modern | same_walk | 1.000  | 1.000  | 0.758  | 0.498  | 0.171 |
| drift_per_ft | 0.12 | modern | cross_session | 1.000  | 1.000  | 0.550  | 0.377  | 0.120 |
| drift_per_ft | 0.16 | modern | cross_session | 1.000  | 1.000  | 0.609  | 0.377  | 0.120 |
| drift_per_ft | 0.2 | modern | cross_session | 1.000  | 1.000  | 0.658  | 0.377  | 0.120 |
| tap_base_ft | 0.15 | modern | cross_session | 1.000  | 1.000  | 0.472  | 0.207  | 0.120 |
| tap_base_ft | 0.3 | modern | cross_session | 1.000  | 1.000  | 0.609  | 0.377  | 0.120 |
| tap_base_ft | 0.6 | modern | cross_session | 1.000  | 1.000  | 0.808  | 0.654  | 0.120 |
| scale_sd_x | 0.5 | modern | cross_session | 1.000  | 1.000  | 0.609  | 0.365  | 0.120 |
| scale_sd_x | 1.0 | modern | cross_session | 1.000  | 1.000  | 0.609  | 0.377  | 0.120 |
| scale_sd_x | 2.0 | modern | cross_session | 1.000  | 1.000  | 0.609  | 0.406  | 0.120 |
| residual_k_x | 0.5 | modern | cross_session | 1.000  | 1.000  | 0.859  | 0.604  | 0.202 |
| residual_k_x | 1.0 | modern | cross_session | 1.000  | 1.000  | 0.609  | 0.377  | 0.120 |
| residual_k_x | 1.5 | modern | cross_session | 1.000  | 1.000  | 0.452  | 0.268  | 0.086 |
| drift_per_ft | 0.12 | phone_2018 | same_walk | 0.916  | 0.891  | 0.258  | 0.185  | 0.056 |
| drift_per_ft | 0.16 | phone_2018 | same_walk | 0.963  | 0.950  | 0.292  | 0.185  | 0.056 |
| drift_per_ft | 0.2 | phone_2018 | same_walk | 0.986  | 0.980  | 0.327  | 0.185  | 0.056 |
| tap_base_ft | 0.15 | phone_2018 | same_walk | 0.957  | 0.950  | 0.216  | 0.109  | 0.056 |
| tap_base_ft | 0.3 | phone_2018 | same_walk | 0.963  | 0.950  | 0.292  | 0.185  | 0.056 |
| tap_base_ft | 0.6 | phone_2018 | same_walk | 0.973  | 0.950  | 0.442  | 0.341  | 0.056 |
| scale_sd_x | 0.5 | phone_2018 | same_walk | 0.963  | 0.950  | 0.292  | 0.172  | 0.056 |
| scale_sd_x | 1.0 | phone_2018 | same_walk | 0.963  | 0.950  | 0.292  | 0.185  | 0.056 |
| scale_sd_x | 2.0 | phone_2018 | same_walk | 0.963  | 0.950  | 0.292  | 0.216  | 0.056 |
| residual_k_x | 0.5 | phone_2018 | same_walk | 1.000  | 1.000  | 0.406  | 0.258  | 0.074 |
| residual_k_x | 1.0 | phone_2018 | same_walk | 0.963  | 0.950  | 0.292  | 0.185  | 0.056 |
| residual_k_x | 1.5 | phone_2018 | same_walk | 0.861  | 0.834  | 0.228  | 0.144  | 0.042 |
| drift_per_ft | 0.12 | phone_2018 | cross_session | 0.670  | 0.635  | 0.168  | 0.121  | 0.033 |
| drift_per_ft | 0.16 | phone_2018 | cross_session | 0.756  | 0.728  | 0.190  | 0.121  | 0.033 |
| drift_per_ft | 0.2 | phone_2018 | cross_session | 0.825  | 0.802  | 0.213  | 0.121  | 0.033 |
| tap_base_ft | 0.15 | phone_2018 | cross_session | 0.742  | 0.728  | 0.141  | 0.069  | 0.033 |
| tap_base_ft | 0.3 | phone_2018 | cross_session | 0.756  | 0.728  | 0.190  | 0.121  | 0.033 |
| tap_base_ft | 0.6 | phone_2018 | cross_session | 0.781  | 0.728  | 0.291  | 0.223  | 0.033 |
| scale_sd_x | 0.5 | phone_2018 | cross_session | 0.756  | 0.728  | 0.190  | 0.111  | 0.033 |
| scale_sd_x | 1.0 | phone_2018 | cross_session | 0.756  | 0.728  | 0.190  | 0.121  | 0.033 |
| scale_sd_x | 2.0 | phone_2018 | cross_session | 0.756  | 0.728  | 0.190  | 0.142  | 0.033 |
| residual_k_x | 0.5 | phone_2018 | cross_session | 0.960  | 0.948  | 0.297  | 0.188  | 0.055 |
| residual_k_x | 1.0 | phone_2018 | cross_session | 0.756  | 0.728  | 0.190  | 0.121  | 0.033 |
| residual_k_x | 1.5 | phone_2018 | cross_session | 0.578  | 0.550  | 0.134  | 0.085  | 0.023 |

