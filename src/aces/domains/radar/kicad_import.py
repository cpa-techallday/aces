"""KiCad layout import for the radar EM verification tier.

Parses a ``.kicad_pcb`` (S-expression) into RF net geometry — segments,
vias, pads, zones — and builds gprMax multiport decks from the *real
routed* copper, closing the last RADAR_EM.md scope item (KiCad-layout →
multiport geometry import).

Deck recipe (all rules earned in earlier jobs):
- Substrate discretised in ``n_sub`` cells (default 4 at h = 0.254 mm,
  cell 0.0635 mm) — allowed since job22 proved the **stacked-source
  feed**: one z-directed voltage source on *every* substrate Ez node
  plane, resistances summing to 50 ohm, port IDs grouped.  A single
  mid-substrate point source inserts a ~35 fF series gap capacitance and
  total-reflects the port (job20/job22).
- Far end terminated by **stacked zero-excitation sources** — the same
  stacked-source string as the feed (resistances summing to 50 ohm) but
  driven by a zero-amplitude waveform, i.e. a true 50 ohm Thevenin
  termination from trace to ground.  This replaced the 2x2 resistive
  column (tile_multiport recipe): generalised to a 4-cell column its
  edge network no longer lands on 50 ohm (measured |Gamma| 0.27 vs
  0.08 for the 2-cell original, job23 first pass), corrupting the
  Z0-from-ripple estimate.
- 3-probe travelling-wave decomposition per port (wave_split_ab) on each
  end's straight section; reference plane = pad/trace junction.
- Only axis-aligned polylines are accepted (staircase-exact on the Yee
  grid); 45-degree or arbitrary-angle routes raise honestly.

Model expectations use Hammerstad/Wheeler **with the 1 oz (35 um) copper
thickness correction** (same form as stackup.py: w_eff = w + (t/pi)(1 +
ln(2h/t))).  The bare adapter model under-reads width and over-reads Z0
by ~9 ohm at w/h = 0.75 (87.8 vs 80.5 ohm); the job23 control run
(straight line, this recipe) measured 78.0 ohm — i.e. the thickness-
corrected model is the one FDTD agrees with to ~3 %.

Measurement note (job23 control): the wave-split S11 of a line fed and
terminated by 50 ohm Thevenin strings is the FAR-END interface reflection
(flat in magnitude, phase rotating 2*beta*L) — the line Z0 is extracted
from the complex termination interface gamma, Z0 = 50(1-G)/(1+G), not
from ripple extrema.  Bends shorten the electrical path (~0.5*w per
unmitered corner), so the delay-implied er_eff of a bent net reads low;
the corner length saving is reported as its own metric.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .em_adapter import microstrip_er_eff, microstrip_z0

C0 = 299792458.0
EPS0 = 8.854187817e-12
CU_T_M = 35e-6          # 1 oz copper, per stackup.py

# Board-1 RF reference gap (stackup.py TILE_8L: F.Cu over In1.Cu GND,
# RO4350B core 0.254 mm).  This is the stackup an imported F.Cu RF net
# sees; it is NOT the antenna-plate stackup (RO4350B in em_adapter is
# h = 0.508 mm for board 2).
TILE_L1L2 = {"name": "RO4350B L1-L2 (board 1)", "substrate_er": 3.66,
             "substrate_h_m": 0.254e-3, "loss_tan": 0.0037}


def _w_eff(w: float, h: float, t: float = CU_T_M) -> float:
    """Thickness-corrected conductor width (stackup.py form)."""
    return w + (t / math.pi) * (1 + math.log(2 * h / t))


def z0_microstrip_t(er: float, h: float, w: float, t: float = CU_T_M) -> float:
    return microstrip_z0(er, h, _w_eff(w, h, t))


def er_eff_microstrip_t(er: float, h: float, w: float, t: float = CU_T_M) -> float:
    return microstrip_er_eff(er, h, _w_eff(w, h, t))


# ------------------------------------------------------------------ S-expr
_TOKEN = re.compile(r'\s*(\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+)')


def _parse_sexpr(s: str) -> list:
    stack, out = [], []
    cur = out
    for m in _TOKEN.finditer(s):
        t = m.group(1)
        if t == "(":
            node: list = []
            cur.append(node)
            stack.append(cur)
            cur = node
        elif t == ")":
            cur = stack.pop()
        else:
            cur.append(t.strip('"') if t.startswith('"') else t)
    return out


def _walk(node, tag, results):
    if isinstance(node, list):
        if node and node[0] == tag:
            results.append(node)
        for ch in node:
            _walk(ch, tag, results)


def _child(node, tag):
    for ch in node:
        if isinstance(ch, list) and ch and ch[0] == tag:
            return ch
    return None


# ------------------------------------------------------------------ model
@dataclass
class Segment:
    start: tuple[float, float]
    end: tuple[float, float]
    width: float          # mm
    layer: str


@dataclass
class Pad:
    net: str
    ref: str
    pad: str
    at: tuple[float, float]   # absolute mm (footprint transform applied, rotation 0/90/180/270 only)
    size: tuple[float, float]


@dataclass
class Pcb:
    nets: dict[int, str] = field(default_factory=dict)
    segments: list = field(default_factory=list)      # list[Segment], with net attr
    vias: list = field(default_factory=list)
    pads: list = field(default_factory=list)          # list[Pad]
    zones: list = field(default_factory=list)         # (net_name, layer)


def parse_pcb(path) -> Pcb:
    """Parse a .kicad_pcb into nets/segments/pads/zones (mm, absolute coords)."""
    tree = _parse_sexpr(Path(path).read_text(encoding="utf-8"))
    pcb = Pcb()
    net_nodes: list = []
    _walk(tree, "net", net_nodes)
    for ch in net_nodes:
        if len(ch) >= 3 and isinstance(ch[2], str):
            pcb.nets[int(ch[1])] = ch[2]

    seg_nodes: list = []
    _walk(tree, "segment", seg_nodes)
    for s in seg_nodes:
        st, en = _child(s, "start"), _child(s, "end")
        seg = Segment((float(st[1]), float(st[2])), (float(en[1]), float(en[2])),
                      float(_child(s, "width")[1]), _child(s, "layer")[1])
        seg.net = pcb.nets.get(int(_child(s, "net")[1]), "?")
        pcb.segments.append(seg)

    via_nodes: list = []
    _walk(tree, "via", via_nodes)
    for v in via_nodes:
        at = _child(v, "at")
        pcb.vias.append({"net": pcb.nets.get(int(_child(v, "net")[1]), "?"),
                         "at": (float(at[1]), float(at[2])),
                         "size": float(_child(v, "size")[1]),
                         "drill": float(_child(v, "drill")[1])})

    fp_nodes: list = []
    _walk(tree, "footprint", fp_nodes)
    for fp in fp_nodes:
        ref = "?"
        for ch in fp:
            if isinstance(ch, list) and ch and ch[0] == "fp_text" and len(ch) > 2 and ch[1] == "reference":
                ref = ch[2]
        at = _child(fp, "at")
        fx, fy = float(at[1]), float(at[2])
        rot = math.radians(float(at[3]) if len(at) > 3 else 0.0)
        ca, sa = round(math.cos(rot)), round(math.sin(rot))
        for ch in fp:
            if not (isinstance(ch, list) and ch and ch[0] == "pad"):
                continue
            n = _child(ch, "net")
            if n is None:
                continue
            pa, sz = _child(ch, "at"), _child(ch, "size")
            px, py = float(pa[1]), float(pa[2])
            pcb.pads.append(Pad(pcb.nets.get(int(n[1]), "?"), ref, ch[1],
                                (fx + ca * px - sa * py, fy + sa * px + ca * py),
                                (float(sz[1]), float(sz[2]))))

    zone_nodes: list = []
    _walk(tree, "zone", zone_nodes)
    for z in zone_nodes:
        nm = _child(z, "net_name")
        lay = _child(z, "layer") or _child(z, "layers")
        if nm and lay:
            pcb.zones.append((nm[1], lay[1]))
    return pcb


# ------------------------------------------------------------------ polyline
def net_polyline(pcb: Pcb, net_name: str, layer: str = "F.Cu") -> dict:
    """Chain a net's segments on one layer into an ordered polyline.

    Returns {"points_mm": [(x, y), ...], "width_mm": w, "pads": [Pad, ...]}.
    Raises on anything the deck builder cannot represent exactly: no
    segments, branching, vias inside the net on this layer, or
    non-axis-aligned segments.
    """
    segs = [s for s in pcb.segments if s.net == net_name and s.layer == layer]
    if not segs:
        raise ValueError(f"net {net_name!r}: no segments on {layer}")
    vias = [v for v in pcb.vias if v["net"] == net_name]
    if vias:
        raise ValueError(f"net {net_name!r}: {len(vias)} via(s) — via transitions "
                         "are not modelled; choose a single-layer run")
    widths = {s.width for s in segs}
    if len(widths) != 1:
        raise ValueError(f"net {net_name!r}: mixed widths {sorted(widths)} on {layer}")
    for s in segs:
        if abs(s.start[0] - s.end[0]) > 1e-9 and abs(s.start[1] - s.end[1]) > 1e-9:
            raise ValueError(f"net {net_name!r}: non-axis-aligned segment "
                             f"{s.start} -> {s.end} (staircase-exact import only)")

    # chain end-to-end
    def key(p):
        return (round(p[0], 4), round(p[1], 4))

    adj: dict = {}
    for s in segs:
        a, b = key(s.start), key(s.end)
        adj.setdefault(a, []).append((b, s))
        adj.setdefault(b, []).append((a, s))
    ends = [p for p, nbrs in adj.items() if len(nbrs) == 1]
    if len(ends) != 2:
        raise ValueError(f"net {net_name!r}: polyline is not a simple chain "
                         f"({len(ends)} endpoints — branching or loops)")
    path = [ends[0]]
    prev, cur = None, ends[0]
    while cur != ends[1]:
        nxt = [n for n, _ in adj[cur] if n != prev]
        if not nxt:
            raise ValueError(f"net {net_name!r}: chain broke at {cur}")
        prev, cur = cur, nxt[0]
        path.append(cur)
    pads = [p for p in pcb.pads if p.net == net_name]
    return {"points_mm": path, "width_mm": segs[0].width, "pads": pads}


# ------------------------------------------------------------------ deck
def deck_kicad_net(polyline: dict, cell: float, window_s: float, f0: float,
                   er: float, h: float, tand: float,
                   feed_end: int = 1, margin_cells: int = 16,
                   air_cells: int = 16, lift_cells: int = 12,
                   n_sub: int | None = None) -> tuple[str, dict]:
    """Build the gprMax deck for an imported net (metres, SI out).

    ``feed_end`` 1 excites the polyline start (load at the end); 2 swaps.
    """
    n_sub = n_sub or max(2, round(h / cell))
    if abs(n_sub * cell - h) > 1e-9:
        raise ValueError(f"substrate h {h} is not {n_sub} cells of {cell}")
    pts_m = [(x * 1e-3, y * 1e-3) for x, y in polyline["points_mm"]]
    w_m = polyline["width_mm"] * 1e-3
    w_cells = max(1, round(w_m / cell))
    w_used = w_cells * cell
    half = w_cells // 2

    xs = [p[0] for p in pts_m]
    ys = [p[1] for p in pts_m]
    # pads extend the polyline ends outward along the end-section axis
    pads = polyline.get("pads", [])

    def end_dir(idx: int) -> tuple[float, float]:
        if idx == 0:
            (x1, y1), (x2, y2) = pts_m[0], pts_m[1]
        else:
            (x1, y1), (x2, y2) = pts_m[-1], pts_m[-2]
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy)
        return dx / n, dy / n

    def pad_at_end(idx: int) -> dict | None:
        ex, ey = pts_m[0] if idx == 0 else pts_m[-1]
        best = None
        for p in pads:
            px, py = p.at[0] * 1e-3, p.at[1] * 1e-3
            if math.hypot(px - ex, py - ey) < 2.5e-3:
                best = p
        return best

    pad_ext = [0.0, 0.0]
    pad_w = [w_used, w_used]
    for i in (0, 1):
        p = pad_at_end(i)
        if p is not None:
            d = end_dir(i)
            along = abs(p.size[0] * d[0]) + abs(p.size[1] * d[1])
            across = abs(p.size[1] * d[0]) + abs(p.size[0] * d[1])
            pad_ext[i] = max(0.0, (along - 0.1) * 1e-3)   # segment starts at pad edge; keep 0.1 mm overlap
            pad_w[i] = max(w_used, round(across * 1e-3 / cell) * cell)

    x_min, x_max = min(xs) - w_used / 2, max(xs) + w_used / 2
    y_min, y_max = min(ys) - w_used / 2, max(ys) + w_used / 2
    for i, (ex, ey) in enumerate((pts_m[0], pts_m[-1])):
        d = end_dir(i)
        x_min = min(x_min, ex - d[0] * pad_ext[i] - pad_w[i] / 2)
        x_max = max(x_max, ex - d[0] * pad_ext[i] + pad_w[i] / 2)
        y_min = min(y_min, ey - d[1] * pad_ext[i] - pad_w[i] / 2)
        y_max = max(y_max, ey - d[1] * pad_ext[i] + pad_w[i] / 2)

    def snap(v: float) -> float:
        return round(v / cell) * cell

    ox, oy = snap(x_min) - margin_cells * cell, snap(y_min) - margin_cells * cell
    dom_x = snap(x_max) + margin_cells * cell - ox
    dom_y = snap(y_max) + margin_cells * cell - oy
    z_gnd = lift_cells * cell
    z_sub = z_gnd + cell
    z_trace = z_sub + n_sub * cell
    dom_z = z_trace + cell + air_cells * cell
    z_probe = z_sub + (n_sub // 2) * cell

    sigma_d = 2 * math.pi * f0 * EPS0 * er * tand
    r_each = 50.0 / n_sub

    def rel(x: float, y: float) -> tuple[float, float]:
        return snap(x - ox), snap(y - oy)

    lines = [f"#title: aces-tile-em kicad_net import ({polyline.get('net', '?')}, feed end {feed_end})",
             f"#domain: {dom_x:.7f} {dom_y:.7f} {dom_z:.7f}",
             f"#dx_dy_dz: {cell:.7f} {cell:.7f} {cell:.7f}",
             f"#time_window: {window_s:.6e}",
             f"#material: {er:.4f} {sigma_d:.5f} 1.0 0.0 sub",
             f"#waveform: gaussiandot 1.0 {f0:.6e} feed",
             f"#waveform: gaussiandot 1.0e-30 {f0:.6e} term",
             f"#box: 0 0 {z_gnd:.7f} {dom_x:.7f} {dom_y:.7f} {z_gnd + cell:.7f} pec",
             f"#box: 0 0 {z_sub:.7f} {dom_x:.7f} {dom_y:.7f} {z_trace:.7f} sub"]

    # trace: one pec box per segment, snapped, w_cells wide
    for i in range(len(pts_m) - 1):
        p1, p2 = pts_m[i], pts_m[i + 1]
        (ax, ay), (bx, by) = rel(*p1), rel(*p2)
        x0, x1 = sorted((ax, bx))
        y0, y1 = sorted((ay, by))
        if abs(y1 - y0) < 1e-12:   # x-running
            y0, y1 = y0 - w_used / 2, y1 + w_used / 2
        else:                      # y-running
            x0, x1 = x0 - w_used / 2, x1 + w_used / 2
        lines.append(f"#box: {x0:.7f} {y0:.7f} {z_trace:.7f} {x1:.7f} {y1:.7f} {z_trace + cell:.7f} pec")

    # pad boxes at the ends (extend outward along the end axis)
    for i, (ex, ey) in enumerate((pts_m[0], pts_m[-1])):
        if pad_ext[i] <= 0:
            continue
        d = end_dir(i)
        cx, cy = rel(ex - d[0] * pad_ext[i] / 2, ey - d[1] * pad_ext[i] / 2)
        pw = pad_w[i] / 2
        if abs(d[1]) > 0.5:   # y-running end
            x0, x1 = cx - pw, cx + pw
            y0, y1 = sorted((cy - pad_ext[i] / 2, cy + pad_ext[i] / 2))
        else:
            y0, y1 = cy - pw, cy + pw
            x0, x1 = sorted((cx - pad_ext[i] / 2, cx + pad_ext[i] / 2))
        lines.append(f"#box: {x0:.7f} {y0:.7f} {z_trace:.7f} {x1:.7f} {y1:.7f} {z_trace + cell:.7f} pec")

    # end geometries: feed point (stacked sources) and load column
    ends = []
    for i, (ex, ey) in enumerate((pts_m[0], pts_m[-1])):
        d = end_dir(i)
        fx, fy = rel(ex + d[0] * cell, ey + d[1] * cell)      # 1 cell inside the trace
        ends.append({"dir": d, "feed_xy": (fx, fy), "end_xy": rel(ex, ey)})

    feed_idx, load_idx = (0, 1) if feed_end == 1 else (1, 0)
    fx, fy = ends[feed_idx]["feed_xy"]
    for k in range(n_sub):
        lines.append(f"#voltage_source: z {fx:.7f} {fy:.7f} {z_sub + k * cell:.7f} "
                     f"{r_each:.3f} feed")
    # termination: identical stacked string at the other end, zero excitation
    # -> true 50 ohm Thevenin load from trace to ground
    lx, ly = ends[load_idx]["feed_xy"]
    for k in range(n_sub):
        lines.append(f"#voltage_source: z {lx:.7f} {ly:.7f} {z_sub + k * cell:.7f} "
                     f"{r_each:.3f} term")

    # probe triads: three points per end along the end-section, starting
    # 0.35 mm inside, 0.40 mm spacing (snapped), kept clear of the corner
    probes = {"near": [], "far": []}
    refs = {}
    for lbl, i in (("near", feed_idx), ("far", load_idx)):
        d = ends[i]["dir"]
        ex, ey = pts_m[0] if i == 0 else pts_m[-1]
        sec = math.hypot(pts_m[1][0] - pts_m[0][0], pts_m[1][1] - pts_m[0][1]) if i == 0 \
            else math.hypot(pts_m[-1][0] - pts_m[-2][0], pts_m[-1][1] - pts_m[-2][1])
        spacing = 4 * cell if 4 * cell < 0.45e-3 else cell  # 0.254 mm at 0.0635 cell
        start = 0.35e-3
        for j in range(3):
            dist = start + j * spacing
            if dist > sec - w_used:
                raise ValueError(f"end section too short for probe triad ({sec * 1e3:.2f} mm)")
            px, py = rel(ex + d[0] * dist, ey + d[1] * dist)
            lines.append(f"#rx: {px:.7f} {py:.7f} {z_probe:.7f}")
            probes[lbl].append(dist)
        refs[lbl] = 0.0
    deck = "\n".join(lines) + "\n"

    er_eff = er_eff_microstrip_t(er, h, w_used)
    total_len = sum(math.hypot(pts_m[i + 1][0] - pts_m[i][0], pts_m[i + 1][1] - pts_m[i][1])
                    for i in range(len(pts_m) - 1))
    geom = {"net": polyline.get("net"), "cell_m": cell, "n_sub_cells": n_sub,
            "line_w_m": w_used, "line_w_layout_m": w_m,
            "er_eff_model": er_eff, "length_m": total_len,
            "probes_near_m": probes["near"], "probes_far_m": probes["far"],
            "feed_end": feed_end, "termination": "stacked 50 ohm Thevenin",
            "z0_model_ohm": z0_microstrip_t(er, h, w_used),
            "stacked_feed": True}
    return deck, geom


def expectations(geom: dict, er: float, h: float, tand: float,
                 f_hz: float) -> dict:
    """Model values the FDTD run is checked against (grid-snapped width,
    thickness-corrected Hammerstad)."""
    w = geom["line_w_m"]
    z0 = z0_microstrip_t(er, h, w)
    er_eff = er_eff_microstrip_t(er, h, w)
    alpha_d = (math.pi * f_hz / C0) * (er / math.sqrt(er_eff)) * \
        ((er_eff - 1) / (er - 1)) * tand     # Np/m, dielectric (PEC conductors)
    loss_db = 8.685889638 * alpha_d * geom["length_m"]
    delay_s = geom["length_m"] * math.sqrt(er_eff) / C0
    return {"z0_model_ohm": z0, "er_eff_model": er_eff,
            "loss_db_model": loss_db, "delay_s_model": delay_s}
