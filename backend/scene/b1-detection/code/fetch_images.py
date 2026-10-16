"""Re-fetch the VM-only evaluation images (PR #16 meter photos and chosen Commons photos)."""

import csv
import time
from pathlib import Path

import requests

from common import B1, WORK

UA = {"User-Agent": "house-scanning-eval/0.1 (research; contact via github SamGu-NRX)"}
API = "https://commons.wikimedia.org/w/api.php"


def get(url, dest):
    if dest.exists() and dest.stat().st_size > 1000:
        return
    for attempt in range(5):
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 200:
            dest.write_bytes(r.content)
            return
        time.sleep(2 ** attempt)
    print("FAILED", dest, r.status_code)


def meters():
    out = WORK / "data/meters_orig"
    out.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(B1 / "vm-copies/meter_manifest.csv")))
    by_title = {r["title"]: r["id"] for r in rows}
    titles = list(by_title)
    for i in range(0, len(titles), 40):
        batch = titles[i : i + 40]
        r = requests.post(
            API,
            data={"action": "query", "prop": "imageinfo", "iiprop": "url", "iiurlwidth": 1600,
                  "titles": "|".join(batch), "format": "json"},
            headers=UA, timeout=60,
        ).json()
        norm = {n["to"]: n["from"] for n in r["query"].get("normalized", [])}
        for p in r["query"]["pages"].values():
            t = norm.get(p["title"], p["title"])
            ii = p.get("imageinfo", [{}])[0]
            url = ii.get("thumburl") or ii.get("url")
            if url and t in by_title:
                get(url, out / f"{by_title[t]}.jpg")
            else:
                print("missing", t)


def commons():
    out = WORK / "data/commons"
    out.mkdir(parents=True, exist_ok=True)
    chosen = {r["id"] for r in csv.DictReader(open(B1 / "commons_houses.csv"))}
    for r in csv.DictReader(open(B1 / "vm-copies/commons_candidates.csv")):
        if r["id"] in chosen:
            get(r["thumb"], out / f"{r['id']}.jpg")


if __name__ == "__main__":
    commons()
    meters()
    print(len(list((WORK / "data/commons").glob("*.jpg"))), len(list((WORK / "data/meters_orig").glob("*.jpg"))))
