"""Run every profile on every scene given; write outputs/results/<scene>__<profile>.json and a table."""
import json, sys, time
from pathlib import Path
import engine3d as E
from run import Y, prof

OUT = Path(__file__).parent / "outputs" / "results"


def main(paths, contract=False):
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in paths:
        sc = E.load_scene(json.loads(Path(p).read_text()))
        for n in Y["profiles"]:
            t = time.time()
            r = E.decide(sc, prof(n, contract))
            r["runtime_s"] = round(time.time() - t, 2)
            r.pop("_cands")
            r["contract_evidence_rule"] = contract
            (OUT / f"{sc.id}__{n}{'__contract' if contract else ''}.json").write_text(json.dumps(r, indent=1))
            b = r["best"]
            rows.append((sc.id + (" [contract]" if contract else ""), n, r["decision"], r["stats"]["pass"], r["stats"]["unsure"], r["stats"]["fail"],
                         f"{b['wall']} {b['u'][0]:.2f}-{b['u'][1]:.2f}" if b else "-", b["route_ft"] if b else "-",
                         ",".join(c["id"] for c in b["checks"] if c["outcome"] != "pass") if b else "", r["runtime_s"]))
            print(*rows[-1], sep=" | ", flush=True)
    return rows


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--contract"]
    main(args, "--contract" in sys.argv)
