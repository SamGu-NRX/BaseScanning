"""Print compact candidate lists for the images on labelling sheets and stage the sheets for viewing.

usage: python cands.py 3 4 5   (sheet numbers)
"""

import json
import shutil
import sys

from common import B1, read_image_list

VIEW = "/workspace/b1view"
HIDE = {"window", "fence", "driveway"}


def main():
    items = [it["id"] for it in read_image_list(B1 / "eval_images_batch1.json")]
    index = json.loads((B1 / "labels/sheet_index.json").read_text())
    for s in sys.argv[1:]:
        s = int(s)
        shutil.copy(B1 / f"labels/sheets/{s:03d}.jpg", f"{VIEW}/s{s:03d}.jpg")
        for i in index[str(s)]:
            c = json.loads((B1 / f"labels/candidates/{i.replace('/', '__')}.json").read_text())
            print(f"## {s} {i}")
            print(" ".join(f"{d['id']}:{d['label'][:4]}[{','.join(f'{v:.2f}' for v in d['box'])}]" for d in c
                           if d["label"] not in HIDE))
    del items


if __name__ == "__main__":
    main()
