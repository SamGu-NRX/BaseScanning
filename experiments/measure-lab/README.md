# Measure Lab

**Question.** Can an iPhone without LiDAR measure what the placement checks need, outdoors, on the first try: a wall's line, points on it, the gap to a facing fence, and an overhead height? The method comes from the [no-LiDAR research note](https://github.com/SamGu-NRX/house-scanning-master/blob/9737e3f0eefe90f2a12a190bf8750e7fed64413f/docs/research/t3-no-lidar-capture.md).

**Pass criteria**, set before any run. Wall and ground distances land within 4 in of the tape, the facing gap and overhead within 6 in, and the 30 ft span within 8 in. Every deliberately bad capture is refused or flagged. The 3 ft decision check never gives a false PASS. [PROTOCOL.md](PROTOCOL.md) has every criterion and the one-hour test.

**Run.** Needs Xcode 26, plus an iPhone on iOS 26 for the field test.

```sh
make test      # geometry tests
make build     # unsigned app build
make project   # regenerate the Xcode project after editing project.yml
```

**Result.** No phone run or accuracy result. `swift test -j 2` passes 114 tests in 17 suites locally. CI run 36290127699 at 7b3ddcc passed the earlier 106-test suite, project drift check, and unsigned build. Visibility and matching the same feature remain operator responsibilities, not verified properties of accepted measurements.

**What changed.** Nothing yet.

## Session format

[SESSION-FORMAT.md](SESSION-FORMAT.md) documents every file and field a session writes.
