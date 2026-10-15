"""Search Wikimedia Commons for openly licensed house-exterior photos showing criteria objects.

Writes candidates to WORK/data/commons_candidates.csv and downloads 1280 px thumbnails to
WORK/data/commons_cand/ for screening by eye. The screened subset goes in commons_houses.csv.
"""

import csv
import json
import re
import time
import urllib.parse
import urllib.request

from common import WORK

UA = {"User-Agent": "base-hackathon-b1-eval/0.1 (research; github.com/SamGu-NRX/house-scanning)"}
API = "https://commons.wikimedia.org/w/api.php"
QUERIES = [
    "air conditioner condenser house exterior",
    "air conditioning unit outside house",
    "gas meter house wall",
    "natural gas meter residential",
    "electric meter house wall",
    "electricity meter outside house",
    "hose bib house",
    "outdoor faucet wall house",
    "downspout house",
    "electrical panel outside house",
    "backyard swimming pool house",
    "house driveway garage suburban",
    "side of house utility meters",
    "backyard fence house",
]
REUSABLE = re.compile(r"^(CC0|Public domain|PD|CC BY(-SA)? [0-9.]+)", re.IGNORECASE)


def api(**params):
    params = {**params, "format": "json", "formatversion": "2"}
    req = urllib.request.Request(API, data=urllib.parse.urlencode(params).encode(), headers=UA)
    for a in range(4):
        try:
            return json.load(urllib.request.urlopen(req, timeout=60))
        except OSError:
            time.sleep(2 + 3 * a)
    raise RuntimeError("api failed")


def main():
    out = WORK / "data/commons_cand"
    out.mkdir(parents=True, exist_ok=True)
    seen, rows = set(), []
    for q in QUERIES:
        d = api(action="query", list="search", srsearch=f"{q} filetype:bitmap", srnamespace="6", srlimit="12")
        titles = [s["title"] for s in d["query"]["search"] if s["title"] not in seen]
        if not titles:
            continue
        info = api(action="query", titles="|".join(titles), prop="imageinfo", iiprop="url|size|extmetadata", iiurlwidth="1280")
        for p in info["query"]["pages"]:
            ii = (p.get("imageinfo") or [{}])[0]
            meta = ii.get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "")
            if not REUSABLE.match(lic) or min(ii.get("width", 0), ii.get("height", 0)) < 700:
                continue
            seen.add(p["title"])
            rid = f"c{len(rows) + 1:03d}"
            rows.append(
                {
                    "id": rid,
                    "title": p["title"],
                    "page_url": ii.get("descriptionurl", ""),
                    "thumb": ii.get("thumburl", ""),
                    "license": lic,
                    "author": re.sub("<[^>]+>", "", meta.get("Artist", {}).get("value", ""))[:80],
                    "query": q,
                }
            )
    for r in rows:
        dest = out / f"{r['id']}.jpg"
        if not dest.exists() and r["thumb"]:
            try:
                dest.write_bytes(urllib.request.urlopen(urllib.request.Request(r["thumb"], headers=UA), timeout=60).read())
            except OSError as e:
                print("fail", r["id"], e)
            time.sleep(0.3)
    with open(WORK / "data/commons_candidates.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(len(rows), "candidates")


if __name__ == "__main__":
    main()
