"""Draw a placement result as a top-down 2D site plan in SVG.

The plan is drawn in the meter wall's frame: the wall runs left to right across the page with the
house above it and the yard below, whatever the scene frame's axes are. Colours: blue meter area,
purple cable, red battery spot and its clearance; gas is utility-locate yellow.
Ground nobody saw is hatched, as in the app's coverage fog. The deciding clearances are drawn as
dimension lines with their measured values, so the drawing shows why the spot works.

No dependency beyond shapely: the SVG is built as text. Colours live in one <style> block with a
dark variant, so the app and the website can show it as is.
"""

import html
import json
import math
import sys
from pathlib import Path
from typing import Any

from shapely import Geometry, LineString, Point, Polygon, box, unary_union
from shapely.affinity import affine_transform, translate
from shapely.ops import nearest_points

from rules import LoadedRules, load_rules
from scene import Piece, Scene, parse_scene
from solver import solve
from units import format_ft_in

WIDTH_PX = 960
PAD_PX = 28
LEGEND_PX = 64
MARGIN_FT = 6.0
# Dimension lines shown for the deciding clearances, closest to their thresholds first.
MAX_DIMENSIONS = 2
# Only clearances this close to their threshold get a dimension line.
DIMENSION_MARGIN_FT = 3.0

STYLE = """
.paper{fill:#F2F4F3}
.ink{stroke:#1D262B;fill:none}
.wall{stroke:#1D262B;stroke-width:5;stroke-linecap:square;fill:none}
.poche{fill:url(#poche)}
.gap{stroke:#1D262B;stroke-width:1.5;stroke-dasharray:4 5;fill:none}
.lawn{fill:#D3E4C8}.mulch{fill:#DDCDB4}.gravel{fill:#D8DAD6}.concrete{fill:#DCDEDC}
.deck{fill:#C9B08C}.drive{fill:#AEB4B9}
.fog{fill:url(#fog)}.fog-line{stroke:#1D262B;stroke-opacity:.22;stroke-width:2}
.poche-line{stroke:#1D262B;stroke-opacity:.35;stroke-width:1}
.opening{stroke:#F2F4F3;stroke-width:5;fill:none}
.opening-mark{stroke:#1D262B;stroke-width:1.2;fill:none}
.object{fill:#FFFFFF;stroke:#1D262B;stroke-width:1.2}
.gas{fill:#F2C200;stroke:#1D262B;stroke-width:1.2}
.gas-ring{fill:none;stroke:#B89200;stroke-width:1.2;stroke-dasharray:3 4}
.pool{fill:#BFE0E8;stroke:#1D262B;stroke-width:1}
.meter-area{fill:#2F6FDB;fill-opacity:.16;stroke:#2F6FDB;stroke-width:1}
.meter{fill:#2F6FDB;stroke:#FFFFFF;stroke-width:2}
.sweep-pass{stroke:#1D262B;stroke-width:3;stroke-opacity:.55}
.sweep-unsure{stroke:#1D262B;stroke-width:3;stroke-opacity:.3;stroke-dasharray:2 3}
.cable{stroke:#7B3FC4;stroke-width:4;stroke-linecap:round;stroke-linejoin:round;fill:none}
.battery{fill:#D7263D;stroke:#7A0F1E;stroke-width:1.5}
.battery.unsure{fill:#D7263D;fill-opacity:.35;stroke-dasharray:5 3}
.battery-ring{fill:none;stroke:#D7263D;stroke-width:1.3;stroke-dasharray:6 4}
.dim{stroke:#1D262B;stroke-width:1}
text{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;fill:#1D262B}
.label{font-size:12px}.small{font-size:11px;fill:#4A565C}
.dim-text{font-size:12px;font-weight:600;paint-order:stroke;stroke:#F2F4F3;stroke-width:4px}
.cable-text{font-size:12px;font-weight:600;fill:#5E2A9E;paint-order:stroke;stroke:#F2F4F3;stroke-width:4px}
@media (prefers-color-scheme:dark){
.paper{fill:#141A1D}.wall,.ink,.gap,.opening-mark,.dim{stroke:#E4E9EB}
.opening{stroke:#141A1D}text{fill:#E4E9EB}.small{fill:#A9B4B9}
.dim-text,.cable-text{stroke:#141A1D}.cable-text{fill:#C9A8F2}
.lawn{fill:#2C3B28}.mulch{fill:#3A3226}.gravel,.concrete{fill:#30363A}.deck{fill:#4A3C29}
.drive{fill:#40474C}.object{fill:#20282C;stroke:#E4E9EB}.pool{fill:#1F3D45}
.sweep-pass,.sweep-unsure{stroke:#E4E9EB}.fog-line,.poche-line{stroke:#E4E9EB}}
"""

DEFS = """<defs>
<pattern id="fog" width="8" height="8" patternUnits="userSpaceOnUse"
 patternTransform="rotate(45)">
<line class="fog-line" x1="0" y1="0" x2="0" y2="8"/></pattern>
<pattern id="poche" width="6" height="6" patternUnits="userSpaceOnUse"
 patternTransform="rotate(-45)">
<line class="poche-line" x1="0" y1="0" x2="0" y2="6"/></pattern>
</defs>"""

OBJECT_NAMES = {
    "window": "Window",
    "door": "Door",
    "garage_door": "Garage",
    "ac": "AC",
    "gas_meter": "Gas",
    "elec_box": "Box",
    "vent": "Vent",
    "downspout": "Downspout",
    "pool": "Pool",
    "battery": "Battery",
}


class _Frame:
    """Maps scene plan points to page pixels in the meter wall's frame."""

    def __init__(self, scene: Scene, focus: Geometry) -> None:
        piece = scene.meter_piece
        self.origin = piece.point(0.0)
        self.along, self.out = piece.along, piece.outward
        minx, miny, maxx, maxy = self._local_bounds(focus)
        self.minu, self.minv = minx - MARGIN_FT, miny - MARGIN_FT
        span_u = (maxx - minx) + 2 * MARGIN_FT
        span_v = (maxy - miny) + 2 * MARGIN_FT
        self.k = (WIDTH_PX - 2 * PAD_PX) / span_u
        self.height = span_v * self.k + 2 * PAD_PX + LEGEND_PX
        self.view_local = box(self.minu, self.minv, self.minu + span_u, self.minv + span_v)

    def local(self, p: tuple[float, float]) -> tuple[float, float]:
        dx, dz = p[0] - self.origin[0], p[1] - self.origin[1]
        return (
            dx * self.along[0] + dz * self.along[1],
            dx * self.out[0] + dz * self.out[1],
        )

    def _local_bounds(self, geom: Geometry) -> tuple[float, float, float, float]:
        pts = [self.local(c) for c in _coords(geom)]
        us, vs = [p[0] for p in pts], [p[1] for p in pts]
        return min(us), min(vs), max(us), max(vs)

    def px(self, p: tuple[float, float]) -> tuple[float, float]:
        return self._page(*self.local(p))

    def _page(self, u: float, v: float) -> tuple[float, float]:
        return ((u - self.minu) * self.k + PAD_PX, (v - self.minv) * self.k + PAD_PX)

    def to_local_geom(self, geom: Geometry) -> Geometry:
        a, b = self.along, self.out
        ox, oz = self.origin
        # u = a.x*x + a.z*z - a.(o); v = b.x*x + b.z*z - b.(o)
        return affine_transform(
            geom, [a[0], a[1], b[0], b[1], -(a[0] * ox + a[1] * oz), -(b[0] * ox + b[1] * oz)]
        )

    def path(self, geom: Geometry) -> str:
        """SVG path data for a scene geometry, clipped to the page."""
        local = self.to_local_geom(geom).intersection(self.view_local)
        parts = []
        for g in getattr(local, "geoms", [local]):
            if g.is_empty:
                continue
            if isinstance(g, Polygon):
                for ring in [g.exterior, *g.interiors]:
                    parts.append(self._ring(ring.coords, close=True))
            elif hasattr(g, "coords"):
                parts.append(self._ring(g.coords, close=False))
        return " ".join(parts)

    def _ring(self, coords: Any, close: bool) -> str:
        pts = [self._page(u, v) for u, v in coords]
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        return d + (" Z" if close else "")


def _coords(geom: Geometry) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for g in getattr(geom, "geoms", [geom]):
        if g.is_empty:
            continue
        if isinstance(g, Polygon):
            out += list(g.exterior.coords)
        else:
            out += list(g.coords)
    return out


def short(feet: float) -> str:
    """ "3 ft" rather than "3 ft 0 in"; "0 ft 7 in" becomes "7 in"."""
    text = format_ft_in(feet)
    if text.endswith(" 0 in"):
        return text[: -len(" 0 in")]
    return text[len("0 ft ") :] if text.startswith("0 ft ") else text


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def render(scene: Scene, result: dict[str, Any], rules: LoadedRules | None = None) -> str:
    """The site plan for one solved scene, as a standalone SVG document."""
    r = (rules or load_rules()).rules
    spot = result.get("spot") or result.get("nearest_considered")
    walls = scene.walls
    focus_parts: list[Geometry] = [LineString([p.a, p.b]) for p in walls]
    focus_parts.append(Point(scene.meter_xz))
    if spot:
        focus_parts.append(Polygon(spot["footprint"]))
    frame = _Frame(scene, unary_union(focus_parts).envelope)
    out: list[str] = []
    w, h = WIDTH_PX, frame.height
    summary = result.get("summary", "")
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h:.0f}" width="{w}" '
        f'height="{h:.0f}" role="img" aria-labelledby="t d">'
    )
    out.append(f'<title id="t">Battery site plan: {_esc(result.get("decision", ""))}</title>')
    out.append(f'<desc id="d">{_esc(summary)}</desc>')
    out.append(f"<style>{STYLE}</style>{DEFS}")
    out.append(f'<rect class="paper" width="{w}" height="{h:.0f}"/>')

    # Ground, then what nobody saw.
    for g in scene.ground:
        d = frame.path(g.polygon)
        if d:
            out.append(f'<path class="{g.type}" d="{d}"/>')
    fog = frame.path(scene.unobserved_ground())
    if fog:
        out.append(f'<path class="fog" d="{fog}"/>')
    for o in scene.objects:
        if o.type == "pool" and isinstance(o.geom, Polygon):
            out.append(f'<path class="pool" d="{frame.path(o.geom)}"/>')

    # The meter's working space (blue) and the house side of each wall (hatched poché).
    ws = r.meter_working_space
    ws_poly = scene.band_polygon(-ws.width_ft.value / 2, ws.width_ft.value / 2, ws.depth_ft.value)
    out.append(f'<path class="meter-area" d="{frame.path(ws_poly)}"/>')
    for p in walls:
        out.append(f'<path class="poche" d="{frame.path(p.rect(p.s0, p.s1, -0.9, 0))}"/>')

    # Where on the wall a battery could stand, on the house side of the wall line.
    width = r.battery.width_ft.value
    for run in result.get("sweep", []):
        if run["outcome"] == "fail":
            continue
        a, b = run["start_ft"][0], run["start_ft"][1] + width
        line = scene.wall_line(a, b)
        piece = scene.piece_at((a + b) / 2)
        shifted = _offset(line, piece, -0.45)
        out.append(f'<path class="sweep-{run["outcome"]}" d="{frame.path(shifted)}"/>')

    for p in walls:
        out.append(f'<path class="wall" d="{frame.path(LineString([p.a, p.b]))}"/>')
    for g in scene.gaps:
        out.append(f'<path class="gap" d="{frame.path(LineString([g.a, g.b]))}"/>')

    # Wall objects: openings break the wall line; equipment sits on or off it.
    gas_ring = r.clearances.gas_ft.value
    for o in scene.objects:
        name = OBJECT_NAMES[o.type]
        if o.type in ("window", "door", "garage_door"):
            line = scene.wall_line(*o.span)
            out.append(f'<path class="opening" d="{frame.path(line)}"/>')
            piece = scene.piece_at(sum(o.span) / 2)
            for off in (-0.25, 0.25):
                out.append(
                    f'<path class="opening-mark" d="{frame.path(_offset(line, piece, off))}"/>'
                )
            _label(out, frame, scene.point_at(sum(o.span) / 2, -1.6), name, "small")
        elif o.type == "gas_meter":
            shape = o.geom.buffer(0.35) if not isinstance(o.geom, Polygon) else o.geom
            out.append(f'<path class="gas-ring" d="{frame.path(o.geom.buffer(gas_ring))}"/>')
            out.append(f'<path class="gas" d="{frame.path(shape)}"/>')
            _label(out, frame, _label_point(scene, o.geom, 1.2), name, "label")
        elif o.type != "pool":
            shape = o.geom.buffer(0.3) if not isinstance(o.geom, Polygon) else o.geom
            out.append(f'<path class="object" d="{frame.path(shape)}"/>')
            _label(out, frame, _label_point(scene, o.geom, 1.2), name, "small")

    # Cable, battery and its clearance.
    route = result.get("route")
    if route and len(route["polyline"]) >= 2:
        line = LineString(route["polyline"])
        out.append(f'<path class="cable" d="{frame.path(line)}"/>')
        mid = line.interpolate(0.5, normalized=True)
        s_mid = scene.s_of((mid.x, mid.y))
        _label(
            out,
            frame,
            scene.piece_at(s_mid).point(s_mid, -2.4),
            f"Cable {short(route['length_ft'])}",
            "cable-text",
        )
    if spot:
        fp = Polygon(spot["footprint"])
        ring = min(r.clearances.gas_ft.value, r.clearances.ac_ft.value)
        out.append(f'<path class="battery-ring" d="{frame.path(fp.buffer(ring, join_style=2))}"/>')
        # A nearest_considered spot is never drawn as a pass.
        passed = result.get("spot") is not None and spot["outcome"] == "pass"
        cls = "battery" if passed else "battery unsure"
        out.append(f'<path class="{cls}" d="{frame.path(fp)}"/>')
        _dimensions(out, frame, scene, fp, result)

    # Meter on top.
    mx, my = frame.px(scene.meter_piece.point(0.0))
    out.append(f'<circle class="meter" cx="{mx:.1f}" cy="{my:.1f}" r="7"/>')
    _label(out, frame, scene.point_at(0.0, -1.6), "Meter", "label")

    _legend(out, frame, result)
    out.append("</svg>")
    return "\n".join(out)


def _offset(line: Geometry, piece: Piece, distance: float) -> Geometry:
    return translate(line, piece.outward[0] * distance, piece.outward[1] * distance)


def _label_point(scene: Scene, geom: Geometry, out: float) -> tuple[float, float]:
    """Things mounted on the wall are named on the house side, in the row with the openings, so
    their names never collide with the dimension lines in the yard. Things out in the yard are
    named just beyond themselves."""
    c = geom.centroid
    s = scene.s_of((c.x, c.y))
    piece = scene.piece_at(s)
    if geom.distance(scene.wall_line(s - 0.01, s + 0.01)) < 0.5:
        return piece.point(s, -1.6)
    _, v = piece.local((c.x, c.y))
    return piece.point(s, max(v, 0) + out)


def _label(out: list[str], frame: _Frame, at: tuple[float, float], text: str, cls: str) -> None:
    x, y = frame.px(at)
    out.append(
        f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
        f'dominant-baseline="middle">{_esc(text)}</text>'
    )


def _dimensions(
    out: list[str], frame: _Frame, scene: Scene, fp: Polygon, result: dict[str, Any]
) -> None:
    """Dimension lines from the battery to the objects that decide its clearances."""
    subjects: dict[str, Geometry] = {o.label: o.geom for o in scene.objects}
    subjects |= {f"ground[{g.index}] {g.type}": g.polygon for g in scene.ground}
    rows = []
    for c in result.get("checks", []):
        geom = subjects.get(c.get("subject") or "")
        if geom is None or c["measured_ft"] is None or c["threshold_ft"] is None:
            continue
        margin = c["measured_ft"] - c["threshold_ft"]
        rows.append((margin, c, geom))
    rows = [row for row in rows if row[0] <= DIMENSION_MARGIN_FT]
    for _, c, geom in sorted(rows, key=lambda row: row[0])[:MAX_DIMENSIONS]:
        a, b = nearest_points(fp, geom)
        if a.distance(b) < 0.25:
            continue
        (x1, y1), (x2, y2) = frame.px((a.x, a.y)), frame.px((b.x, b.y))
        ang = math.atan2(y2 - y1, x2 - x1)
        tx, ty = -math.sin(ang) * 5, math.cos(ang) * 5
        out.append(f'<line class="dim" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
        for x, y in ((x1, y1), (x2, y2)):
            out.append(
                f'<line class="dim" x1="{x - tx:.1f}" y1="{y - ty:.1f}" '
                f'x2="{x + tx:.1f}" y2="{y + ty:.1f}"/>'
            )
        label = f"{short(c['measured_ft'])} (min {short(c['threshold_ft'])})"
        lx, ly = (x1 + x2) / 2 + tx * 3.2, (y1 + y2) / 2 + ty * 3.2 + 4
        out.append(
            f'<text class="dim-text" x="{lx:.1f}" y="{ly:.1f}" '
            f'text-anchor="middle">{_esc(label)}</text>'
        )


def _legend(out: list[str], frame: _Frame, result: dict[str, Any]) -> None:
    y = frame.height - LEGEND_PX + 26
    x = PAD_PX
    # Scale bar: 5 ft.
    bar = 5 * frame.k
    out.append(
        f'<line class="ink" x1="{x}" y1="{y}" x2="{x + bar:.1f}" y2="{y}" stroke-width="2"/>'
    )
    for t in (0, bar / 2, bar):
        out.append(
            f'<line class="ink" x1="{x + t:.1f}" y1="{y - 4}" x2="{x + t:.1f}" y2="{y + 4}"/>'
        )
    out.append(f'<text class="small" x="{x + bar + 8:.1f}" y="{y + 4}">5 ft</text>')
    items = [
        ("battery", "Battery"),
        ("cable-key", "Cable"),
        ("meter-area", "Meter working space"),
        ("gas", "Gas"),
        ("fog", "Not seen"),
    ]
    cx = x + bar + 64
    for cls, text in items:
        if cls == "cable-key":
            out.append(f'<line class="cable" x1="{cx}" y1="{y}" x2="{cx + 18}" y2="{y}"/>')
        else:
            out.append(f'<rect class="{cls}" x="{cx}" y="{y - 7}" width="18" height="14"/>')
        out.append(f'<text class="small" x="{cx + 24}" y="{y + 4}">{_esc(text)}</text>')
        cx += 32 + 7.2 * len(text)
    decision = {"pass": "Fits", "manual_review": "Needs review", "reject": "No spot"}.get(
        result.get("decision", ""), ""
    )
    out.append(
        f'<text class="label" x="{WIDTH_PX - PAD_PX}" y="{y + 4}" text-anchor="end" '
        f'font-weight="600">{_esc(decision)}</text>'
    )


def main(argv: list[str]) -> int:
    """python siteplan.py scene.json out.svg: solve a scene and write its site plan."""
    if len(argv) != 3:
        print(main.__doc__, file=sys.stderr)
        return 2
    loaded = load_rules()
    raw = json.loads(Path(argv[1]).read_text())
    scene = parse_scene(raw, loaded.rules)
    Path(argv[2]).write_text(render(scene, solve(scene, loaded), loaded))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
