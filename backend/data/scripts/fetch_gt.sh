#!/usr/bin/env bash
# Download laser ground truth that is kept out of the store.
#   fetch_gt.sh eth3d <facade|electro|delivery_area> [DEST]   -> DEST/<scene>/dslr_scan_eval/*.ply
#   fetch_gt.sh tt barn [DEST]                                -> DEST/Barn.ply (+ SfM log, alignment, crop)
# DEST defaults to /tmp/house-gt. Needs curl and 7z (apt-get install p7zip-full) / unzip.
set -euo pipefail
kind=$1; scene=$2; dest=${3:-/tmp/house-gt}
mkdir -p "$dest"; cd "$dest"
case "$kind" in
  eth3d)
    curl -fL -C - -o "${scene}_dslr_scan_eval.7z" "https://www.eth3d.net/data/${scene}_dslr_scan_eval.7z"
    7z x -y "${scene}_dslr_scan_eval.7z" > /dev/null
    echo "scan: $dest/$scene/dslr_scan_eval/ (poses in ../../houses/eth3d-$scene/calibration/images.txt are in the same frame)"
    ;;
  tt)
    [ "$scene" = barn ] || { echo "only barn is curated"; exit 1; }
    dl() { curl -fL -o "$2" "https://drive.usercontent.google.com/download?id=$1&export=download&confirm=t${3:+&resourcekey=$3}"; }
    # The "ground truth" link serves the raw binary PLY, not a zip.
    dl 0B-ePgl6HF260UEVoYTVFX0V1MXM Barn.ply 0-4yH5seXqNeaMtpBMozvVmw
    echo "gt: $dest/Barn.ply (poses/alignment/crop are already in houses/tt-barn/calibration/)"
    ;;
  *) echo "usage: $0 eth3d|tt <scene> [dest]"; exit 1;;
esac
