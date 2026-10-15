"""Union raw jsonl runs by photo id across directories, folding in store sync conflict copies.

    python merge_raw.py SRC [SRC ...] DEST

For each <set>/<engine>.jsonl (and <engine>.<stamp>.conflict.jsonl) under any SRC, DEST gets one
file per engine with one row per id: the first non-error row seen, else the last error row.
Conflict copies in DEST are removed once folded in.
"""

import json
import re
import sys
from pathlib import Path


CONFLICT = re.compile(r"\.\d{4}-\d\d-\d\dT[^.]*\.[^.]+\.conflict\.jsonl$")


def engine_of(path: Path) -> str:
    return CONFLICT.sub("", path.name) if CONFLICT.search(path.name) else path.stem


def main() -> None:
    *sources, dest = (Path(a) for a in sys.argv[1:])
    merged: dict[tuple[str, str], dict[str, dict]] = {}
    seen_files = []
    for src in [dest, *sources]:
        for path in sorted(src.glob("*/*.jsonl")):
            seen_files.append(path)
            rows = merged.setdefault((path.parent.name, engine_of(path)), {})
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                old = rows.get(record["id"])
                if old is None or ("error" in old and "error" not in record):
                    rows[record["id"]] = record
    for (set_name, engine), rows in sorted(merged.items()):
        out = dest / set_name / f"{engine}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for _, r in sorted(rows.items())))
        tmp.replace(out)
        ok = sum("error" not in r for r in rows.values())
        print(f"{set_name}/{engine}: {ok} ok, {len(rows) - ok} error")
    for path in seen_files:
        if CONFLICT.search(path.name) and path.parent.parent == dest and path.exists():
            path.unlink()


if __name__ == "__main__":
    main()
