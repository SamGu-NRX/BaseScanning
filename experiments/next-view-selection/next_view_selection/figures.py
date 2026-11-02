"""Deterministic figures for the study summary (regenerated identically on replay)."""

from __future__ import annotations

from pathlib import Path

FIGURE_SPECS = (
    ("fig_resolution.png", "resolution_rate", "resolution rate", (0.0, 1.05)),
    ("fig_cost.png", "mean_cost", "mean action cost (budget units)", (0.0, None)),
    ("fig_unsupported.png", "unsupported_rate", "unsupported definite answer rate", (0.0, 1.05)),
)


def write_figures(summary_rows: list[dict[str, object]], out_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    policies = [str(row["policy"]) for row in summary_rows]
    for filename, key, label, ylim in FIGURE_SPECS:
        values = [float(row[key]) for row in summary_rows]
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar(policies, values, color="#4477aa")
        ax.set_ylabel(label)
        ax.set_title(f"{label} by policy")
        top = max(values) * 1.2 + 0.01 if ylim[1] is None else ylim[1]
        ax.set_ylim(ylim[0], top)
        for tick in ax.get_xticklabels():
            tick.set_rotation(20)
            tick.set_ha("right")
        fig.tight_layout()
        path = out_dir / filename
        fig.savefig(path, metadata={"Software": None})
        plt.close(fig)
        written.append(path)
    return written
