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

import numpy as np

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


def net_sections(pcb: Pcb, net_name: str) -> dict:
    """Chain a multi-layer net into ordered sections + vias (CAL_* topology).

    Returns {"sections": [{"layer": str, "points_mm": [...], "width_mm": w}],
             "vias_mm": [(x, y, size, drill)], "pads": [...]}.
    Sections are ordered end-to-end; a via joins the end of one section to
    the start of the next.  Same honesty rules as net_polyline: branches,
    loops and non-axis-aligned segments raise.
    """
    segs = [s for s in pcb.segments if s.net == net_name]
    if not segs:
        raise ValueError(f"net {net_name!r}: no segments")
    vias = [v for v in pcb.vias if v["net"] == net_name]
    for s in segs:
        if abs(s.start[0] - s.end[0]) > 1e-9 and abs(s.start[1] - s.end[1]) > 1e-9:
            raise ValueError(f"net {net_name!r}: non-axis-aligned segment "
                             f"{s.start} -> {s.end} (staircase-exact import only)")

    def key(p):
        return (round(p[0], 4), round(p[1], 4))

    # nodes: segment endpoints and via sites; edges: segments (layered) and vias
    adj: dict = {}
    for s in segs:
        a, b = key(s.start), key(s.end)
        adj.setdefault(a, []).append((b, s))
        adj.setdefault(b, []).append((a, s))
    via_sites = {key(v["at"]) for v in vias}
    ends = [p for p, nbrs in adj.items() if len(nbrs) == 1]
    if len(ends) != 2:
        raise ValueError(f"net {net_name!r}: not a simple chain ({len(ends)} endpoints)")

    # walk, emitting a new section whenever the layer changes; a layer change
    # is only legal at a via site (copper on different layers cannot touch)
    sections = []
    prev, cur = None, ends[0]
    cur_layer = None
    hops = 0
    visited = {ends[0]}
    while True:
        nxt = [(n, s) for n, s in adj.get(cur, [])
               if not (n == prev) and not (n in visited and n != ends[1])]
        if not nxt:
            break
        n, s = nxt[0]
        if s.layer != cur_layer:
            if cur_layer is not None:
                if cur not in via_sites:
                    raise ValueError(f"net {net_name!r}: layer change at {cur} "
                                     "without a via")
                hops += 1
            sections.append({"layer": s.layer, "points_mm": [cur], "width_mm": s.width})
            cur_layer = s.layer
        sections[-1]["points_mm"].append(n)
        visited.add(n)
        prev, cur = cur, n
        if cur == ends[1]:
            break
    if not sections or sections[-1]["points_mm"][-1] != ends[1]:
        raise ValueError(f"net {net_name!r}: chain did not reach the far end")
    if hops != len(vias):
        raise ValueError(f"net {net_name!r}: {len(vias)} via(s) but the chain "
                         f"uses {hops} — stub vias or unrouted sections")
    pads = [p for p in pcb.pads if p.net == net_name]
    return {"sections": sections,
            "vias_mm": [(v["at"][0], v["at"][1], v["size"], v["drill"]) for v in vias],
            "n_vias_used": hops, "pads": pads}


# ------------------------------------------------------------------ via coupon
def stripline_z0_t(er: float, b: float, w: float, t: float = CU_T_M) -> float:
    """Symmetric stripline, IPC-2141 form with thickness correction
    (stackup.py z0_stripline).  ``b`` is the full plane-to-plane spacing."""
    we = w + (t / math.pi) * (1 - 0.5 * math.log((t / (2 * b + t)) ** 2
                                                 + (0.0796 * t / (w + 1.1 * t)) ** 2))
    return (60 / math.sqrt(er)) * math.log(4 * b / (0.67 * math.pi * (0.8 * we + t)))


def stripline_z0_cohn(er: float, b: float, w: float, t: float = CU_T_M) -> float:
    """Symmetric stripline, Cohn (1955) fringing form — accurate to ~1-2 %
    for w/(b-t) >= 0.35 where IPC-2141 increasingly under-predicts
    (job25: coupon cross-section w/(b-t) = 0.75, IPC-2141 27.1 ohm vs
    Cohn 35.9 ohm vs 2-D Laplace SOR field solve 35.6 ohm).
    ``b`` is the plane-to-plane spacing."""
    u = 1.0 - t / b
    cf = ((2.0 / u) * math.log(1.0 / u + 1.0)
          - (1.0 / u - 1.0) * math.log(1.0 / u ** 2 - 1.0)) / math.pi
    return 94.15 / (math.sqrt(er) * ((w / b) / u + cf))


def cascade_sparams(freqs_hz, sections, z_port: float = 50.0) -> list[dict]:
    """ABCD cascade between z_port ports.  Element kinds:
      - line: {"z0": ohm, "er_eff": float, "length_m": float,
               optional "tand": float, "er": float, "kind": "microstrip"|"stripline"}
        (dielectric loss included when tand is given; PEC conductors)
      - series inductance: {"series_l_h": float}   (via barrel model)
    Returns per-frequency {"s11": c, "s21": c}.
    """
    out = []
    for f in freqs_hz:
        m = np.eye(2, dtype=complex)
        for s in sections:
            if "series_l_h" in s:
                z = 1j * 2 * math.pi * f * s["series_l_h"]
                ms = np.array([[1, z], [0, 1]], dtype=complex)
            else:
                beta = 2 * math.pi * f * math.sqrt(s["er_eff"]) / C0
                alpha = 0.0
                tand = s.get("tand", 0.0)
                if tand:
                    er, ee = s["er"], s["er_eff"]
                    if s.get("kind") == "stripline":
                        alpha = (math.pi * f * math.sqrt(er) / C0) * tand
                    else:
                        alpha = (math.pi * f / C0) * (er / math.sqrt(ee)) * \
                            ((ee - 1) / (er - 1)) * tand
                g = alpha + 1j * beta
                gl = g * s["length_m"]
                z0 = s["z0"]
                ch, sh = np.cosh(gl), np.sinh(gl)
                ms = np.array([[ch, z0 * sh], [sh / z0, ch]], dtype=complex)
            m = m @ ms
        a, b, c_, d = m[0, 0], m[0, 1], m[1, 0], m[1, 1]
        denom = a + b / z_port + c_ * z_port + d
        s21 = 2 / denom
        s11 = (a + b / z_port - c_ * z_port - d) / denom
        out.append({"frequency_hz": float(f), "s11": s11, "s21": s21})
    return out


def via_inductance_h(length_m: float, radius_m: float) -> float:
    """Via barrel series inductance (Grover wire form, isolated-barrel
    approximation): L = 2e-7 * h * (ln(2h/r) - 0.75)."""
    return 2e-7 * length_m * (math.log(2 * length_m / radius_m) - 0.75)


def deck_via_coupon(cell: float, window_s: float, f0: float,
                    l_ms: float = 6e-3, l_sl: float = 6e-3,
                    w_ms: float = 0.508e-3, w_sl: float = 0.2032e-3,
                    via_cells: int = 4, antipad_cells: int = 10,
                    stitch_cells: int = 2, stitch_off: float = 0.508e-3,
                    feed_end: int = 1, margin_cells: int = 16,
                    air_cells: int = 16, lift_cells: int = 12,
                    er_ro: float = 3.66, tand_ro: float = 0.0037,
                    er_fr4: float = 4.10, tand_fr4: float = 0.021) -> tuple[str, dict]:
    """Via-transition coupon on the board-1 hybrid stack (TILE_8L L1..L3):

      F.Cu microstrip (RO4350B, h = 0.254 mm = 5 cells @ 0.0508) -- via -->
      In2.Cu stripline (FR4, b = 7 cells = 0.356 mm plane inner-face
      spacing incl. the trace cell; 0.324 mm layout) -- via --> F.Cu.

    Solid In3 GND below, In1 GND with square antipads at the vias, via
    barrels as square PEC columns (drill 0.20 mm -> 4 cells = 0.2032 mm),
    4 diagonal stitching vias per signal via (45 deg offsets — axis-aligned
    stitches land on the trace and short it).  Stacked-source
    feed/termination across the 5-cell RO4350B gap (5 x 10 ohm, job22
    rule).  Reference = ABCD cascade of the three lossy line sections
    (Cohn stripline Z0; stitched via loop-L negligible at X-band).
    """
    n_ro = round(0.254e-3 / cell)
    n_fr4 = round(0.162e-3 / cell)
    if abs(n_ro * cell - 0.254e-3) > 1e-9:
        raise ValueError("cell must divide the 0.254 mm RO4350B core")

    def snap(v: float) -> float:
        return round(v / cell) * cell

    l_ms, l_sl = snap(l_ms), snap(l_sl)
    w_ms_c, w_sl_c = round(w_ms / cell), round(w_sl / cell)
    w_ms_u, w_sl_u = w_ms_c * cell, w_sl_c * cell
    total = 2 * l_ms + l_sl

    dom_x = total + 2 * margin_cells * cell
    y_span = max(w_ms_u, w_sl_u, antipad_cells * cell)
    dom_y = y_span + 2 * margin_cells * cell
    yc = snap(dom_y / 2)
    z_in3 = lift_cells * cell
    z_in2 = z_in3 + cell + n_fr4 * cell
    z_in1 = z_in2 + cell + n_fr4 * cell
    z_fc = z_in1 + cell + n_ro * cell
    dom_z = z_fc + cell + air_cells * cell
    z_probe = z_in1 + cell + (n_ro // 2) * cell

    sigma_ro = 2 * math.pi * f0 * EPS0 * er_ro * tand_ro
    sigma_fr4 = 2 * math.pi * f0 * EPS0 * er_fr4 * tand_fr4

    x0 = margin_cells * cell
    x_v1 = snap(x0 + l_ms)
    x_v2 = snap(x0 + l_ms + l_sl)
    x_end = snap(x0 + total)

    lines = ["#title: aces-tile-em via_coupon (F.Cu -> via -> In2 -> via -> F.Cu)",
             f"#domain: {dom_x:.7f} {dom_y:.7f} {dom_z:.7f}",
             f"#dx_dy_dz: {cell:.7f} {cell:.7f} {cell:.7f}",
             f"#time_window: {window_s:.6e}",
             f"#material: {er_ro:.4f} {sigma_ro:.5f} 1.0 0.0 subro",
             f"#material: {er_fr4:.4f} {sigma_fr4:.5f} 1.0 0.0 subfr4",
             f"#waveform: gaussiandot 1.0 {f0:.6e} feed",
             f"#waveform: gaussiandot 1.0e-30 {f0:.6e} term",
             # dielectrics
             f"#box: 0 0 {z_in3 + cell:.7f} {dom_x:.7f} {dom_y:.7f} {z_in2:.7f} subfr4",
             f"#box: 0 0 {z_in2 + cell:.7f} {dom_x:.7f} {dom_y:.7f} {z_in1:.7f} subfr4",
             f"#box: 0 0 {z_in1 + cell:.7f} {dom_x:.7f} {dom_y:.7f} {z_fc:.7f} subro",
             # reference planes
             f"#box: 0 0 {z_in3:.7f} {dom_x:.7f} {dom_y:.7f} {z_in3 + cell:.7f} pec",
             f"#box: 0 0 {z_in1:.7f} {dom_x:.7f} {dom_y:.7f} {z_in1 + cell:.7f} pec"]
    # antipads: re-stamp dielectric through the In1 plane at each via
    for xv in (x_v1, x_v2):
        ap = antipad_cells * cell / 2
        lines.append(f"#box: {xv - ap:.7f} {yc - ap:.7f} {z_in1:.7f} "
                     f"{xv + ap:.7f} {yc + ap:.7f} {z_in1 + cell:.7f} subfr4")
    # traces
    hw_m, hw_s = w_ms_u / 2, w_sl_u / 2
    lines.append(f"#box: {x0:.7f} {yc - hw_m:.7f} {z_fc:.7f} {x_v1:.7f} {yc + hw_m:.7f} {z_fc + cell:.7f} pec")
    lines.append(f"#box: {x_v2:.7f} {yc - hw_m:.7f} {z_fc:.7f} {x_end:.7f} {yc + hw_m:.7f} {z_fc + cell:.7f} pec")
    lines.append(f"#box: {x_v1:.7f} {yc - hw_s:.7f} {z_in2:.7f} {x_v2:.7f} {yc + hw_s:.7f} {z_in2 + cell:.7f} pec")
    # via barrels: square columns through the whole stack between the traces
    hv = via_cells * cell / 2
    for xv in (x_v1, x_v2):
        lines.append(f"#box: {xv - hv:.7f} {yc - hv:.7f} {z_in2:.7f} "
                     f"{xv + hv:.7f} {yc + hv:.7f} {z_fc + cell:.7f} pec")
    # stitching vias: 4 ground barrels (In1 <-> In3) at 45-degree offsets
    # around each signal via — closing the return path across the transition
    # WITHOUT landing on the trace axis (job25: (±off, 0) stitches short the
    # stripline trace, total reflection)
    hs = stitch_cells * cell / 2
    d45 = stitch_off / math.sqrt(2)
    for xv in (x_v1, x_v2):
        for dx, dy in ((d45, d45), (d45, -d45), (-d45, d45), (-d45, -d45)):
            xs_, ys_ = snap(xv + dx), snap(yc + dy)
            lines.append(f"#box: {xs_ - hs:.7f} {ys_ - hs:.7f} {z_in3:.7f} "
                         f"{xs_ + hs:.7f} {ys_ + hs:.7f} {z_in1 + cell:.7f} pec")

    # feed / termination strings across the RO4350B gap (job22 rule)
    r_each = 50.0 / n_ro
    x_p1 = snap(x0 + cell)
    x_p2 = snap(x_end - cell)
    fx, tx = (x_p1, x_p2) if feed_end == 1 else (x_p2, x_p1)
    for k in range(n_ro):
        lines.append(f"#voltage_source: z {fx:.7f} {yc:.7f} {z_in1 + cell + k * cell:.7f} "
                     f"{r_each:.3f} feed")
    for k in range(n_ro):
        lines.append(f"#voltage_source: z {tx:.7f} {yc:.7f} {z_in1 + cell + k * cell:.7f} "
                     f"{r_each:.3f} term")

    # probe triads on the two F.Cu sections; the FED end's triad comes first
    # (rx1-3) so the runner's near/far split matches every feed_end
    spacing = 4 * cell if 4 * cell < 0.45e-3 else cell
    x_feed_end = x0 if feed_end == 1 else x_end
    x_term_end = x_end if feed_end == 1 else x0
    sgn = 1.0 if feed_end == 1 else -1.0
    near, far = [], []
    for j in range(3):
        d = 0.35e-3 + j * spacing
        lines.append(f"#rx: {snap(x_feed_end + sgn * d):.7f} {yc:.7f} {z_probe:.7f}")
        near.append(d)
    for j in range(3):
        d = 0.35e-3 + j * spacing
        lines.append(f"#rx: {snap(x_term_end - sgn * d):.7f} {yc:.7f} {z_probe:.7f}")
        far.append(d)

    er_eff_ms = er_eff_microstrip_t(er_ro, n_ro * cell, w_ms_u)
    # plane inner-face spacing: FR4 cells below + trace cell + FR4 cells above
    # (job25: modelling b as 2*n_fr4 cells forgot the trace cell -> 27 ohm
    #  IPC-2141 instead of the correct 36 ohm Cohn)
    b_sl = (2 * n_fr4 + 1) * cell
    z0_sl = stripline_z0_cohn(er_fr4, b_sl, w_sl_u, t=cell)
    via_l = z_fc - z_in2                       # barrel length (full stack)
    via_r = via_cells * cell / 2               # square barrel half-width
    # via vertical transit: FR4 (trace cell + n_fr4) + RO4350B (n_ro) cells;
    # the plane cells are PEC.  Per via; the delay model adds 2x.
    via_transit = ((n_fr4 + 1) * cell * math.sqrt(er_fr4)
                   + n_ro * cell * math.sqrt(er_ro)) / C0
    geom = {"cell_m": cell, "l_ms_m": l_ms, "l_sl_m": l_sl,
            "w_ms_m": w_ms_u, "w_sl_m": w_sl_u,
            "via_cells": via_cells, "antipad_cells": antipad_cells,
            "via_l_m": via_l, "via_r_m": via_r,
            "via_l_h": via_inductance_h(via_l, via_r),
            "stitch_cells": stitch_cells, "stitch_off_m": stitch_off,
            "er_ro": er_ro, "tand_ro": tand_ro, "er_fr4": er_fr4, "tand_fr4": tand_fr4,
            "er_eff_ms_model": er_eff_ms,
            "z0_ms_model_ohm": z0_microstrip_t(er_ro, n_ro * cell, w_ms_u),
            "z0_sl_model_ohm": z0_sl, "er_sl": er_fr4,
            "z0_sl_ipc2141_ohm": stripline_z0_t(er_fr4, b_sl, w_sl_u, t=cell),
            "via_transit_s": via_transit,
            "b_sl_modelled_m": b_sl, "b_sl_layout_m": 0.324e-3,
            "probes_near_m": near, "probes_far_m": far,
            "feed_end": feed_end, "length_m": total,
            "stacked_feed": True}
    return "\n".join(lines) + "\n", geom


def expectations_via_coupon(geom: dict, f_hz: float) -> dict:
    """Cascade-model reference for the via coupon: lossy line sections
    (dielectric only, PEC conductors).  With >= 4 stitching vias per signal
    via the loop inductance of the transition is negligible at X-band
    (job25 fit: L_eff < 0.02 nH; isolated-barrel Grover 0.16 nH
    over-predicts), so no series-L elements.  Group delay includes the
    two via vertical transits."""
    ms = {"z0": geom["z0_ms_model_ohm"], "er_eff": geom["er_eff_ms_model"],
          "length_m": geom["l_ms_m"], "kind": "microstrip",
          "er": geom["er_ro"], "tand": geom["tand_ro"]}
    sl = {"z0": geom["z0_sl_model_ohm"], "er_eff": geom["er_sl"],
          "length_m": geom["l_sl_m"], "kind": "stripline",
          "er": geom["er_fr4"], "tand": geom["tand_fr4"]}
    sections = [ms, sl, ms]
    sp = cascade_sparams([f_hz], sections)[0]
    delay = sum(s.get("length_m", 0.0) * math.sqrt(s["er_eff"]) / C0
                for s in sections if "er_eff" in s)
    delay += 2 * geom.get("via_transit_s", 0.0)
    return {"s11_model": sp["s11"], "s21_model": sp["s21"],
            "delay_s_model": delay, "sections": sections}


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
