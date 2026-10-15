# ETH3D facade, COLMAP reference

Renders of the COLMAP tight-prior reconstruction of the ETH3D facade. The PLY files are the A1 COLMAP runs of that same facade, one cloud per run. Open them in MeshLab or CloudCompare.

ETH3D-derived. Evaluation use only.

## Renders

- `inputs-contact-sheet.jpg` — contact sheet of the input photos.
- `sparse-oblique.jpg` — sparse reconstruction, oblique view.
- `sparse-topdown.jpg` — sparse reconstruction, top-down.
- `sparse-vs-laser.jpg` — sparse cloud laid over the laser scan.
- `keydistances.jpg` — key distances on the reconstruction.
- `turntable.mp4` — turntable of the reconstruction.

## Point clouds

Each file is `point-clouds/<run-name>.ply`.

- `arkit_nonlidar_modern_s0_prior_sparse.ply` — non-LiDAR ARKit position prior, sparse.
- `arkit_nonlidar_modern_s0_fixed_sparse.ply` — non-LiDAR ARKit poses held fixed, sparse.
- `arkit_lidar_modern_s0_prior_sparse.ply` — LiDAR ARKit position prior, sparse.
- `arkit_lidar_modern_s0_fixed_sparse.ply` — LiDAR ARKit poses held fixed, sparse.
- `arkit_lidar_modern_s0_fixed_dense.ply` — LiDAR ARKit poses held fixed, dense.
- `incremental_sparse.ply` — incremental SfM, sparse.
- `incremental_dense.ply` — incremental SfM, dense.
- `global_sparse.ply` — global SfM, sparse.
- `gt_sparse.ply` — ground-truth camera poses, sparse.
- `gt_dense.ply` — ground-truth camera poses, dense.
