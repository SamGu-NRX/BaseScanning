"""Score a WS-06 scene against laser ground truth.

Missing laser or occluder PLYs are an error (SD-61). ``--allow-missing-occluders``
is debug-only: the result is provisional and this command exits 3 so a gate
cannot treat it as a pass. The geometry numbers themselves come from
``python -m server.eval.cli score-geometry``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from server.common.occluders import check_occluder_geometry, gate_rejects_provisional
from server.eval.cli import main as eval_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gt", type=Path, required=True)
    parser.add_argument("--recon", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-missing-occluders", action="store_true")
    parser.add_argument("--no-visibility", action="store_true")
    args = parser.parse_args(argv)
    gt = json.loads(args.gt.read_text())
    recon = json.loads(args.recon.read_text())
    status = check_occluder_geometry(
        (gt, args.gt.parent),
        (recon, args.recon.parent),
        allow_missing=args.allow_missing_occluders,
    )
    if gate_rejects_provisional(status):
        print(json.dumps({"provisional": True, "missing": status["missing"]}))
        return 3
    command = ["score-geometry", "--gt", str(args.gt), "--recon", str(args.recon), "--out", str(args.out)]
    if args.no_visibility:
        command.append("--no-visibility")
    return eval_main(command)


if __name__ == "__main__":
    sys.exit(main())
