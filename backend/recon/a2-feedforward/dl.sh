#!/bin/bash
set -e
cd ~/a2/dl
for s in delivery_area electro facade; do for k in dslr_scan_eval dslr_occlusion dslr_undistorted; do
  f=${s}_${k}.7z
  [ -f done_$f ] && continue
  echo "$(date +%T) downloading $f"
  curl -sSL --retry 5 -o $f https://www.eth3d.net/data/$f
  sha256sum $f >> sha256.txt
  echo "$(date +%T) extracting $f"
  python3 -c "import py7zr,sys; py7zr.SevenZipFile(sys.argv[1]).extractall(sys.argv[2])" $f ~/a2/data/eth3d
  rm $f; touch done_$f
done; done
echo ALL_DONE
