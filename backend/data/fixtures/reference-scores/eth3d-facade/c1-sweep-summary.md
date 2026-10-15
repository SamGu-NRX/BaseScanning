# C1 error sweep summary

2268 runs. Ground truth per scene x profile: delivery_area-D1/C=manual_review, delivery_area-D1/public=manual_review, delivery_area-D1/strictest=manual_review, delivery_area-D3/C=pass, delivery_area-D3/public=manual_review, delivery_area-D3/strictest=manual_review, facade-M1/C=pass, facade-M1/public=reject, facade-M1/strictest=manual_review, facade-M3/C=pass, facade-M3/public=reject, facade-M3/strictest=manual_review

| level | mode | runs | flips | unsafe pass | pass | manual_review | reject (GT not reject) | lost GT pass | errors |
|---|---|---|---|---|---|---|---|---|---|
| uniform_0in | informed | 48 | 0% | 0 (0%) | 25% | 58% | 0 | 0/12 | 0 |
| uniform_2in | informed | 48 | 2% | 1 (2%) | 23% | 60% | 0 | 1/12 | 0 |
| uniform_2in | naive | 48 | 2% | 2 (4%) | 23% | 60% | 0 | 1/12 | 0 |
| uniform_4in | informed | 48 | 8% | 0 (0%) | 17% | 67% | 0 | 4/12 | 0 |
| uniform_4in | naive | 48 | 2% | 3 (6%) | 23% | 60% | 0 | 1/12 | 0 |
| uniform_6in | informed | 48 | 12% | 1 (2%) | 12% | 71% | 0 | 6/12 | 0 |
| uniform_6in | naive | 48 | 2% | 3 (6%) | 23% | 60% | 0 | 1/12 | 0 |
| uniform_8in | informed | 48 | 21% | 0 (0%) | 4% | 79% | 0 | 10/12 | 0 |
| uniform_8in | naive | 48 | 4% | 4 (8%) | 21% | 62% | 0 | 2/12 | 0 |
| uniform_12in | informed | 48 | 40% | 0 (0%) | 0% | 98% | 0 | 12/12 | 0 |
| uniform_12in | naive | 48 | 8% | 5 (10%) | 17% | 67% | 0 | 4/12 | 0 |
| uniform_18in | informed | 48 | 42% | 0 (0%) | 0% | 100% | 0 | 12/12 | 0 |
| uniform_18in | naive | 48 | 8% | 5 (10%) | 17% | 67% | 0 | 4/12 | 0 |
| uniform_27in | informed | 48 | 42% | 0 (0%) | 0% | 100% | 0 | 12/12 | 0 |
| uniform_27in | naive | 48 | 17% | 3 (6%) | 8% | 75% | 0 | 8/12 | 0 |
| uniform_36in | informed | 48 | 42% | 0 (0%) | 0% | 100% | 0 | 12/12 | 0 |
| uniform_36in | naive | 48 | 19% | 2 (4%) | 6% | 77% | 0 | 9/12 | 0 |
| scale_-10pct | informed | 48 | 42% | 0 (0%) | 0% | 100% | 0 | 12/12 | 0 |
| scale_-10pct | naive | 48 | 0% | 0 (0%) | 25% | 58% | 0 | 0/12 | 0 |
| scale_-5pct | informed | 48 | 42% | 0 (0%) | 0% | 100% | 0 | 12/12 | 0 |
| scale_-5pct | naive | 48 | 0% | 0 (0%) | 25% | 58% | 0 | 0/12 | 0 |
| scale_-2pct | informed | 48 | 25% | 0 (0%) | 0% | 83% | 0 | 12/12 | 0 |
| scale_-2pct | naive | 48 | 0% | 0 (0%) | 25% | 58% | 0 | 0/12 | 0 |
| scale_-1pct | informed | 48 | 0% | 0 (0%) | 25% | 58% | 0 | 0/12 | 0 |
| scale_-1pct | naive | 48 | 0% | 0 (0%) | 25% | 58% | 0 | 0/12 | 0 |
| scale_+1pct | informed | 48 | 0% | 0 (0%) | 25% | 58% | 0 | 0/12 | 0 |
| scale_+1pct | naive | 48 | 0% | 4 (8%) | 25% | 58% | 0 | 0/12 | 0 |
| scale_+2pct | informed | 48 | 17% | 0 (0%) | 8% | 75% | 0 | 8/12 | 0 |
| scale_+2pct | naive | 48 | 0% | 4 (8%) | 25% | 58% | 0 | 0/12 | 0 |
| scale_+5pct | informed | 48 | 42% | 0 (0%) | 0% | 100% | 0 | 12/12 | 0 |
| scale_+5pct | naive | 48 | 0% | 12 (25%) | 25% | 58% | 0 | 0/12 | 0 |
| scale_+10pct | informed | 48 | 42% | 0 (0%) | 0% | 100% | 0 | 12/12 | 0 |
| scale_+10pct | naive | 48 | 0% | 12 (25%) | 25% | 58% | 0 | 0/12 | 0 |
| mvd_good | informed | 48 | 8% | 1 (2%) | 17% | 67% | 0 | 4/12 | 0 |
| mvd_good | naive | 48 | 4% | 4 (8%) | 21% | 62% | 0 | 2/12 | 0 |
| mvd_mid | informed | 48 | 12% | 1 (2%) | 12% | 71% | 0 | 6/12 | 0 |
| mvd_mid | naive | 48 | 4% | 4 (8%) | 21% | 62% | 0 | 2/12 | 0 |
| mvd_poor | informed | 48 | 29% | 0 (0%) | 0% | 88% | 0 | 12/12 | 0 |
| mvd_poor | naive | 48 | 6% | 4 (8%) | 19% | 65% | 0 | 3/12 | 0 |
| photo_tape | informed | 48 | 42% | 0 (0%) | 0% | 100% | 0 | 12/12 | 0 |
| photo_tape | naive | 48 | 10% | 5 (10%) | 15% | 69% | 0 | 5/12 | 0 |
| arkit_lidar_modern | informed | 48 | 42% | 0 (0%) | 0% | 100% | 0 | 12/12 | 0 |
| arkit_lidar_modern | naive | 48 | 10% | 3 (6%) | 15% | 69% | 0 | 5/12 | 0 |
| arkit_nonlidar_modern | informed | 48 | 42% | 0 (0%) | 0% | 100% | 0 | 12/12 | 0 |
| arkit_nonlidar_modern | naive | 48 | 10% | 0 (0%) | 15% | 69% | 0 | 5/12 | 0 |
| arkit_nonlidar_2018 | informed | 48 | 42% | 0 (0%) | 0% | 100% | 0 | 12/12 | 0 |
| arkit_nonlidar_2018 | naive | 48 | 19% | 0 (0%) | 6% | 77% | 0 | 9/12 | 0 |

Unsafe passes by profile (level, mode, profile: unsafe/runs):

- uniform_2in informed C: 1/16
- uniform_2in naive C: 2/16
- uniform_4in naive C: 3/16
- uniform_6in informed C: 1/16
- uniform_6in naive C: 3/16
- uniform_8in naive C: 4/16
- uniform_12in naive C: 5/16
- uniform_18in naive C: 5/16
- uniform_27in naive C: 3/16
- uniform_36in naive C: 2/16
- scale_+1pct naive C: 4/16
- scale_+2pct naive C: 4/16
- scale_+5pct naive C: 12/16
- scale_+10pct naive C: 12/16
- mvd_good informed C: 1/16
- mvd_good naive C: 4/16
- mvd_mid informed C: 1/16
- mvd_mid naive C: 4/16
- mvd_poor naive C: 4/16
- photo_tape naive C: 5/16
- arkit_lidar_modern naive C: 3/16
