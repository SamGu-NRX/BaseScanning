import numpy as np

from server.scale.fuse import Source, fuse
from server.scale.sim import a8_terms as terms
from server.scale.sim import no_tape_mc as monte_carlo
from server.scale.sim.no_tape_mc import main


def test_simulation_smoke_returns_the_published_row_names():
    rows = main(n=80)
    names = {row["method"] for row in rows}
    assert "ARKit modern only" in names
    assert "Two meter close-ups (head-on + ~30 deg), exact model (+ARKit modern)" in names
    assert any(name.startswith("Reference: marked length") for name in names)
    assert any(name.startswith("Recommended: meter anchor (two close-ups)") for name in names)
    assert len(rows) == 23


def _product_source(source, index: int) -> Source | None:
    chosen = source
    if getattr(source, "fallback", None) is not None and not bool(source.known[index]):
        chosen = source.fallback
    if not chosen.present[index]:
        return None
    sd = chosen.sd[index] if isinstance(chosen.sd, np.ndarray) else chosen.sd
    weights = None if chosen.weights is None else np.asarray(chosen.weights, float)
    if weights is not None and getattr(weights, "ndim", 1) == 2:
        weights = weights[index]
    return Source(
        chosen.name,
        float(chosen.o[index]),
        float(sd),
        offsets=None if chosen.offsets is None else np.asarray(chosen.offsets, float),
        weights=weights,
        student=chosen.student,
    )


def test_product_fuse_matches_the_simulation_on_the_same_draws():
    """The product posterior is the one ``run.py`` uses. It must match the simulator behind item 9."""

    terms.reset(400)
    monte_carlo.OFFCAT["w"] = 0.0
    sources = monte_carlo._sources()
    policies = {
        "two_closeups": [sources["ark_m"], sources["meter_two"]],
        "arkit_modern": [sources["ark_m"]],
    }
    for name, chosen in policies.items():
        simulated, _bound, _covered = monte_carlo.fuse(chosen)
        product = []
        for index in range(terms.N):
            rows = [row for source in chosen if (row := _product_source(source, index)) is not None]
            product.append(float(np.expm1(fuse(rows).log_est)))
        product = np.asarray(product)
        assert np.max(np.abs(product - simulated)) < 0.002, name
    two = monte_carlo.fuse([sources["ark_m"], sources["meter_two"]])[0]
    # Published n=20000 seed-7 median is 0.60 %. A 400-draw prefix stays in that neighbourhood.
    assert abs(100 * np.median(np.abs(two)) - 0.60) < 1.5
