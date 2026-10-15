# Fixture licences

These files are for private evaluation in this repository. Do not publish the tree.

ETH3D scenes, labels that describe them, verdicts and reference scores are derived from the ETH3D multi-view benchmark (facade, electro, delivery_area). ETH3D is CC BY-NC-SA 4.0: non-commercial, share-alike, evaluation only. The laser and occluder PLYs are not in git. `make fixtures` copies them into `.fixtures/`, and `scripts/rebuild_gt.sh` can rebuild them from the ETH3D archives.

YouTube walkaround frames stay in the packet tars under `.fixtures/packets/`. Those tars are fetched, never committed. The committed `packet.*.json` files are synthetic metadata (`source.kind: synthetic` in the generated packets; the mini packet is built from the Appendix B example). Tanks and Temples (`tt-barn`) is the same: the tar is fetched, the manifest JSON is committed, and the dataset licence is still to be confirmed before any use outside evaluation.

`data/fixtures/packets/examples/` and `data/fixtures/packets/mini-arkit/` are synthetic. They contain no customer photo, no Base prompt text, and no real meter number.

`FIXTURE_SOURCE` defaults to the local store `internal/` directory. The same relative paths are the layout for `gs://hss-fixtures` once that bucket exists (WS-12 also writes this mirror as `gs://hss-fixtures-<env>`). The bucket is not published yet, so a `gs://` fetch fails until it is.
