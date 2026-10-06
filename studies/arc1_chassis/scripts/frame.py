"""ARC-1 :: macro space frame - topology, FEA, sizing.

Level 1 of the hierarchy. Nodes sit on station rings inside the lofted
envelope; members are lattice booms (level 2, see boom.py). This file decides
WHERE the structure goes and HOW BIG each boom must be, by solving it.

Run:  python scripts/frame.py            # analyse + report
      python scripts/frame.py --dump     # also write out/arc1_frame.json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from boom import Boom, A_ROD, E, G, MAT, PR, D_ROD

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad" /
                __import__("os").environ.get("ARC1_PARAMS",
                                             "params.json")).read_text())
OUT = ROOT / P["output"]["dir"]

RING_ANGLES = [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5]
NR = len(RING_ANGLES)
TOP_SEG = (1, 2)          # ring segment that crosses the cockpit opening
FLOOR_RING = (5, 6)       # ring indices forming the floor

# boom radius by member class (mm)
CLASS_R = {
    "ring":      30.0,
    "ring_floor": 34.0,
    "longeron":  32.0,
    "diagonal":  22.0,
    "bulkhead":  28.0,
    "hoop":      34.0,
    "hoop_brace": 30.0,
    "pickup":    34.0,
    "floor_x":   26.0,
    "roof_x":    22.0,
    "side_x":    24.0,
}

# Optional overrides from params (added for ACES design studies; rev A sets none, so it is unchanged):
#   "frame": {"class_R_mm": {"pickup": 40.0},
#             "pickup_links_per_quadrant": 2}                      every hard point
#             "pickup_links_per_quadrant": {"FLA_L_F": 2, "default": 1}   per hard point (mirrors follow)
FRAME_OPT = P.get("frame", {})
CLASS_R.update({k: float(v) for k, v in FRAME_OPT.get("class_R_mm", {}).items()})
_PL = FRAME_OPT.get("pickup_links_per_quadrant", 1)


def pickup_links(hp_id: str) -> int:
    if not isinstance(_PL, dict):
        return int(_PL)
    base = hp_id[:-2] if hp_id.endswith("_M") else hp_id
    return int(_PL.get(hp_id, _PL.get(base, _PL.get("default", 1))))

# Rings are forced at every suspension pickup x: a pickup that does not land on
# a ring can only hang off the frame in bending, which is what the first
# analysis showed (12x over capacity, 642 N.m of link moment).
PICKUP_X = sorted({float(p["xyz"][0]) for p in P["hard_points"]["points"]})

# ---- bonded carbon skin, modelled as equivalent shear-panel diagonals ----
SKIN = {"on": False,
        "t_mm": 0.75,        # 3 plies of 200 gsm twill
        "G_MPa": 20000.0,    # mixed +-45 / 0-90 laminate in-plane shear
        "areal_kg_m2": 1.30, # laminate + resin
        "adhesive_kg_m2": 0.40}
SKIN_AREA = [0.0]            # accumulated panel area, m2


def skin_section(a, b, E_model):
    """A shear panel a x b of thickness t and modulus G is equivalent to a
    cross-brace of this area. Panel k = a*t*G/b; one diagonal k = a^2*A*E/d^3;
    two diagonals share, so A = t*G*d^3 / (2*a*b*E)."""
    d = math.hypot(a, b)
    A = SKIN["t_mm"] * SKIN["G_MPa"] * d ** 3 / (2.0 * a * b * E_model)
    return {"A": A, "I": 1e-3, "J": 1e-3}


def add_skin_panel(f, n00, n01, n10, n11, tag):
    """Two equivalent diagonals across the quad, and book the panel area."""
    import numpy as _np
    p = [_np.array(f.nodes[i]) for i in (n00, n01, n10, n11)]
    a = float(_np.linalg.norm(p[2] - p[0]))      # along the bay
    b = float(_np.linalg.norm(p[1] - p[0]))      # around the ring
    if a < 1e-6 or b < 1e-6:
        return
    SKIN_AREA[0] += a * b / 1e6
    CLASS_R[tag] = skin_section(a, b, E)
    f.member(n00, n11, tag)
    f.member(n01, n10, tag)


def env_at(x: float) -> dict:
    """Interpolate the lofted envelope section at any x."""
    sts = P["envelope"]["stations"]
    xs = np.array([s["x"] for s in sts], float)
    out = {}
    for key in ("half_width", "height", "z_floor", "n"):
        out[key] = float(np.interp(x, xs, [s[key] for s in sts]))
    out["x"] = float(x)
    return out


def frame_stations() -> list[dict]:
    """Envelope stations, with the two suspension stations substituted in for
    the single station they straddle."""
    xs = [s["x"] for s in P["envelope"]["stations"]]
    keep = [x for x in xs
            if not any(abs(x - px) < 120.0 for px in PICKUP_X)]
    allx = sorted(set(keep) | set(PICKUP_X))
    return [env_at(x) for x in allx]


def superellipse_pt(a, b, n_top, n_bot, deg):
    t = math.radians(deg)
    ct, st = math.cos(t), math.sin(t)
    n = n_bot if st < 0 else n_top
    e = 2.0 / n
    y = a * (0.0 if ct == 0 else math.copysign(abs(ct) ** e, ct))
    z = b * (0.0 if st == 0 else math.copysign(abs(st) ** e, st))
    return y, z


class Frame:
    def __init__(self):
        self.nodes: list[tuple[float, float, float]] = []
        self.members: list[tuple[int, int, str]] = []
        self.labels: dict[int, str] = {}
        self._key: dict[tuple, int] = {}

    def node(self, x, y, z, label=None) -> int:
        k = (round(x, 2), round(y, 2), round(z, 2))
        if k in self._key:
            return self._key[k]
        i = len(self.nodes)
        self.nodes.append((float(x), float(y), float(z)))
        self._key[k] = i
        if label:
            self.labels[i] = label
        return i

    def member(self, i, j, cls):
        if i == j:
            return
        a, b = (i, j) if i < j else (j, i)
        # One boom per node pair. The ONLY thing allowed to sit on top of an
        # existing member is a bonded skin panel, which is a genuine second
        # load path across the bay - not a second boom in the same place.
        skin = cls.startswith("skin_")
        for (p, q, c) in self.members:
            if p == a and q == b and (c == cls or not (skin or c.startswith("skin_"))):
                return
        self.members.append((a, b, cls))

    def length(self, m):
        i, j, _ = m
        return float(np.linalg.norm(
            np.array(self.nodes[i]) - np.array(self.nodes[j])))


def build() -> Frame:
    env, ck, hp = P["envelope"], P["cockpit"], P["hard_points"]
    f = Frame()
    # booms inside the envelope set the inset; pickup links run outboard to the hard points, so a
    # bigger pickup boom must not shrink the tub (rev A: 34 mm either way)
    inset = max(v for k, v in CLASS_R.items() if k != "pickup" and not isinstance(v, dict)) + 6.0

    # ---- station rings ---------------------------------------------
    stations = frame_stations()
    rings = []
    for st in stations:
        x, h = float(st["x"]), float(st["height"])
        a = float(st["half_width"]) - inset
        b = h / 2.0 - inset
        if a < 25 or b < 25:                       # nose is too small to ring
            rings.append(None)
            continue
        zc = float(st["z_floor"]) + h / 2.0
        n_top = float(st["n"])
        n_bot = min(n_top * 1.7, 8.0)
        ring = []
        for k, deg in enumerate(RING_ANGLES):
            y, z = superellipse_pt(a, b, n_top, n_bot, deg)
            ring.append(f.node(x, y, zc + z, f"STA{int(x)}_R{k}"))
        rings.append(ring)

    # nose apex: a single node the front rings converge on
    st0 = stations[0]
    nose = f.node(st0["x"] + 40.0, 0.0,
                  st0["z_floor"] + st0["height"] / 2.0, "NOSE")

    live = [(i, r) for i, r in enumerate(rings) if r is not None]

    def si_at(x):
        return min(range(len(stations)), key=lambda i: abs(stations[i]["x"] - x))

    # ---- ring members ----------------------------------------------
    for si, ring in live:
        x = float(stations[si]["x"])
        in_cockpit = ck["x_front"] - 1 <= x <= ck["x_rear"] + 1
        for k in range(NR):
            seg = (k, (k + 1) % NR)
            if in_cockpit and seg == TOP_SEG:
                continue                            # keep the cockpit open
            cls = "ring_floor" if seg == FLOOR_RING else "ring"
            f.member(ring[k], ring[(k + 1) % NR], cls)

    # ---- longerons + bay diagonals ---------------------------------
    for (sa, ra), (sb, rb) in zip(live, live[1:]):
        for k in range(NR):
            f.member(ra[k], rb[k], "longeron")
        # one diagonal per longitudinal quad, direction alternating per bay
        flip = (sa % 2 == 0)
        for k in range(NR):
            kk = (k + 1) % NR
            if flip:
                f.member(ra[k], rb[kk], "diagonal")
            else:
                f.member(ra[kk], rb[k], "diagonal")
        # Shear panels. The floor is the chassis torsion box and the single
        # most effective place to spend mass; the roof only where the cockpit
        # is not open.
        f.member(ra[5], rb[6], "floor_x")
        f.member(ra[6], rb[5], "floor_x")
        # Full X on both side panels. With the cockpit roof open, the sides
        # and floor are the only closed shear paths left, and torsional
        # stiffness lives or dies on them.
        for (p, q) in ((7, 0), (0, 7), (3, 4), (4, 3)):
            f.member(ra[p], rb[q], "side_x")
        xm = 0.5 * (stations[sa]["x"] + stations[sb]["x"])
        in_ck = ck["x_front"] - 1 <= xm <= ck["x_rear"] + 1
        if not in_ck:
            f.member(ra[1], rb[2], "roof_x")
            f.member(ra[2], rb[1], "roof_x")
        if SKIN["on"]:
            # the skin wraps every ring segment of every bay; the cockpit
            # aperture is the one place it cannot close
            for k in range(NR):
                kk = (k + 1) % NR
                if in_ck and (k, kk) == TOP_SEG:
                    continue
                add_skin_panel(f, ra[k], ra[kk], rb[k], rb[kk],
                               f"skin_{sa}_{k}")

    # nose cone: first live ring back to the apex
    for k in range(NR):
        f.member(nose, live[0][1][k], "diagonal")

    # ---- bulkhead cross-bracing ------------------------------------
    # full X-brace at the structural bulkheads, floor-only in the cockpit
    for si, ring in live:
        x = float(stations[si]["x"])
        in_cockpit = ck["x_front"] - 1 <= x <= ck["x_rear"] + 1
        # An engine bay cannot have an X-brace through the middle of it. Ring
        # + longerons only there; the engine or its subframe closes the bay.
        open_bay = any(a <= x <= b for a, b in P.get("open_bays", []))
        if open_bay:
            f.member(ring[5], ring[6], "bulkhead")          # floor tie only
        elif in_cockpit:
            f.member(ring[5], ring[6], "bulkhead")          # floor tie
            f.member(ring[0], ring[3], "bulkhead")          # waist tie
        else:
            f.member(ring[0], ring[3], "bulkhead")
            f.member(ring[5], ring[2], "bulkhead")
            f.member(ring[6], ring[1], "bulkhead")

    # ---- roll hoops -------------------------------------------------
    def hoop(x, apex_z, si, label):
        ring = rings[si]
        pR, pL = np.array(f.nodes[ring[1]]), np.array(f.nodes[ring[2]])
        top_z = max(pR[2], pL[2])
        prev_r, prev_l = ring[1], ring[2]
        n = 3
        for s in range(1, n + 1):
            t = s / n
            y = float(pR[1]) * (1.0 - t)
            z = top_z + (apex_z - top_z) * math.sin(t * math.pi / 2.0)
            if s == n:
                a = f.node(x, 0.0, apex_z, f"{label}_APEX")
                f.member(prev_r, a, "hoop")
                f.member(prev_l, a, "hoop")
                return a
            nr = f.node(x, y, z, f"{label}_R{s}")
            nl = f.node(x, -y, z, f"{label}_L{s}")
            f.member(prev_r, nr, "hoop")
            f.member(prev_l, nl, "hoop")
            prev_r, prev_l = nr, nl
        return None

    main_apex = hoop(1600.0, 1100.0, si_at(1600.0), "MAINHOOP")
    hoop(900.0, 760.0, si_at(900.0), "FRONTHOOP")

    # main hoop rearward braces - mandatory in every rulebook, and the
    # single biggest contributor to rear torsional stiffness
    for k in (1, 2):
        f.member(main_apex, rings[si_at(1950.0)][k], "hoop_brace")

    # ---- suspension hard points ------------------------------------
    pts = list(hp["points"])
    if hp.get("mirror_y"):
        pts += [{"id": p["id"] + "_M",
                 "xyz": [p["xyz"][0], -p["xyz"][1], p["xyz"][2]],
                 "note": p["note"]} for p in hp["points"]]

    n_frame = len(f.nodes)          # everything after this is a pickup node
    node_arr = np.array(f.nodes)
    hard = {}
    for p in pts:
        xyz = np.array(p["xyz"], float)
        hid = f.node(*xyz, p["id"])
        hard[p["id"]] = hid

        # Only consider frame nodes on the same side of the car - a link that
        # crosses the centreline is not a suspension pickup, it is a bulkhead.
        side = np.sign(xyz[1]) if abs(xyz[1]) > 1e-6 else 1.0
        cand = [k for k in range(n_frame)
                if node_arr[k][1] * side > -40.0]

        # nearest node in each of the four (fore/aft x, up/down z) quadrants:
        # four non-coplanar links resolve any load direction axially
        for sx in (-1, 1):
            for sz in (-1, 1):
                ranked = []
                for k in cand:
                    dx = node_arr[k][0] - xyz[0]
                    dz = node_arr[k][2] - xyz[2]
                    if dx * sx <= 25.0 or dz * sz <= 25.0:
                        continue
                    ranked.append((float(np.linalg.norm(node_arr[k] - xyz)), k))
                for _, best in sorted(ranked)[:pickup_links(p["id"])]:
                    f.member(hid, int(best), "pickup")

    return f, hard


# ---------------------------------------------------------------------
def J_for(R: float) -> float:
    cal = json.loads((OUT / "boom_calibration.json").read_text())
    xs = np.array(sorted(float(k) for k in cal))
    ys = np.array([cal[str(int(x))]["J_eq_mm4"] for x in xs])
    return float(np.interp(R, xs, ys))


from fea import analyse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--skin", action="store_true",
                    help="bond a carbon skin over the lattice")
    a = ap.parse_args()

    if a.skin:
        SKIN["on"] = True
    f, hard = build()
    res = analyse(f, hard, CLASS_R)
    if a.skin:
        area = SKIN_AREA[0]
        kg = area * (SKIN["areal_kg_m2"] + SKIN["adhesive_kg_m2"])
        res["skin_area_m2"] = round(area, 2)
        res["skin_mass_kg"] = round(kg, 2)
        print(f"CARBON SKIN  {area:.2f} m2 @ {SKIN['t_mm']} mm  ->  "
              f"{kg:.1f} kg  (laminate {SKIN['areal_kg_m2']} + adhesive "
              f"{SKIN['adhesive_kg_m2']} kg/m2)")

    if a.dump:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "arc1_frame.json").write_text(json.dumps({
            "nodes": f.nodes,
            "members": [[i, j, c] for i, j, c in f.members],
            "labels": {str(k): v for k, v in f.labels.items()},
            "hard": hard,
            "class_R": CLASS_R,
            "result": res,
        }, indent=1))
        print(f"\nwrote {OUT / 'arc1_frame.json'}")


if __name__ == "__main__":
    main()
