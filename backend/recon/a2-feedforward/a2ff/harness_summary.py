"""Table of shared-scorer (evalharness score-cloud) results for the A2 runs: python -m a2ff.harness_summary"""

from __future__ import annotations

import json

from a2ff.harness_run import OUT


def row(method: str, variant: str, g: dict) -> str:
    kd = g["key_distances"]
    o, t = kd["overall"], kd["on_truly_observed_elements"]
    sc = g["scale"]
    al = sc.get("alignment", {})
    scale = al.get("cloud_scale_error_pct", sc.get("scale_error_pct"))
    comp = g["completeness"]
    st = kd["status_counts"]

    def s(x):
        return "n/a" if not x.get("n") else f"{x['median_in']:.1f} / {x['p90_in']:.1f} (n={x['n']})"

    def c(region):
        r = comp.get(region, {})
        return f"{100 * r.get('after_icp', {}).get('within_10cm', float('nan')):.0f}%"

    fo = comp.get("wall_face", {}).get("coverage", {}).get("false_observed_by_rule")
    return (f"| {method} | {variant} | {st['scored']}/{st['missing']}/{st['phantom']} | {s(o)} "
            f"| {100 * o.get('share_le_4in', 0):.0f}% | {s(t)} | {len(kd['false_observed_claims'])} "
            f"| {'n/a' if scale is None else f'{scale:+.1f}%'} | {g['anchor']['icp_translation_norm_m']:.2f} "
            f"| {c('meter_wall')} | {c('wall_face')} | {c('ground_strip')} | {fo} |")


def main() -> None:
    lines = [
        "| Method | Variant | KDs scored/missing/phantom | All KDs median / p90 in | <=4 in | Truly observed KDs "
        "median / p90 in | False-observed claims | Scale error | Anchor m | Meter wall <=10 cm | Wall face <=10 cm "
        "| Ground strip <=10 cm | Wall-face false-observed by rule |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for f in sorted(OUT.glob("*/*/score*/geomscore.json")):
        g = json.loads(f.read_text())
        variant = f.parts[-3]
        if f.parts[-2] == "score_orient":
            al = json.loads((f.parent.parent / "alignment.json").read_text())
            g["scale"].setdefault("alignment", {})["cloud_scale_error_pct"] = (
                al["cloud_scale_error_pct"] if al["scale_applied"] == 1.0 else None)
            variant += " (rot+centre aligned)"
        else:
            variant += " (harness centre-only)"
        lines.append(row(f.parts[-4], variant, g))
    md = "\n".join(lines) + "\n"
    (OUT / "summary.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
