#!/usr/bin/env bash
# Copy small A1 results from the VM (~/a1/work) into the store: results/<scene>/...
set -u
W=~/a1/work
OUT="$(dirname "$0")/results"
for S in facade electro delivery_area; do
  mkdir -p "$OUT/$S/sparse" "$OUT/$S/dense" "$OUT/$S/scores"
  cp -f $W/$S/features.json "$OUT/$S/" 2>/dev/null
  cp -f $W/$S/sparse/*.json "$OUT/$S/sparse/" 2>/dev/null
  cp -f $W/$S/mvs/*.json "$OUT/$S/dense/" 2>/dev/null
  cp -f $W/$S/scores/*.json "$OUT/$S/scores/" 2>/dev/null
  for m in incremental arkit_lidar_modern_s0_prior arkit_nonlidar_modern_s0_prior; do
    for d in $W/$S/sparse/$m $W/$S/sparse/_hide_$m; do
      # Light model: poses without 2D observations, points without tracks (enough for score-cloud).
      if [ -d "$d" ]; then
        T=$(mktemp -d); cp "$d/cameras.txt" "$T/"
        awk '/^#/ {print; next} {n++; if (n % 2 == 1) print; else print ""}' "$d/images.txt" > "$T/images.txt"
        awk '/^#/ {print; next} {print $1, $2, $3, $4, $5, $6, $7, $8}' "$d/points3D.txt" > "$T/points3D.txt"
        tar -czf "$OUT/$S/sparse/$m.model-light.tar.gz" -C "$T" cameras.txt images.txt points3D.txt; rm -rf "$T"
      fi
    done
  done
done
for H in yt-4h8kyjnPo9s yt-0gotY_syWWU yt-5-M-7f35Tyg; do
  mkdir -p "$OUT/online/$H"
  cp -f $W/$H/report.json $W/$H/plan.png "$OUT/online/$H/" 2>/dev/null
  cp -f ~/a1/logs/online_$H.log.tail "$OUT/online/$H/" 2>/dev/null
done
grep -v "^\s" ~/a1/logs/online_yt-4h8kyjnPo9s.log | grep -E "Keeping|Registering|Traceback|Error" | tail -20 > "$OUT/online/yt-4h8kyjnPo9s/sfm-and-crash.txt"
tail -3 ~/a1/work/yt-4h8kyjnPo9s/mvs/densify_r1.log >> "$OUT/online/yt-4h8kyjnPo9s/sfm-and-crash.txt"
du -sh "$OUT"
