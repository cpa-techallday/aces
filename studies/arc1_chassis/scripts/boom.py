"""ARC-1 :: the lattice boom - the unit that replaces a steel tube.

A macro "member" of the chassis is not a tube. It is a 3-chord micro-truss:
three 2.4 mm rods on a circle of radius R, tied every 60 mm (the buckling bay)
by three battens and three diagonals. Everything the frame model needs about a
member - axial area, bending inertia, torsion constant, mass - comes from here.

Torsion is the honest part. A laced boom is far softer in torsion than the tube
it replaces, because torque is carried only by the diagonal lacing. GJ is
therefore CALIBRATED against a directly-solved Pynite model of one boom rather
than assumed. Run this file to see the calibration.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad" /
                __import__("os").environ.get("ARC1_PARAMS",
                                             "params.json")).read_text())
PR, MAT = P["process"], P["material"]

D_ROD = PR["rod_diameter_mm"]
A_ROD = math.pi * (D_ROD / 2.0) ** 2
I_ROD = math.pi * D_ROD ** 4 / 64.0
BAY = PR["strut_len_max_mm"]
RHO = MAT["density_g_cm3"]
E = MAT["E_MPa"]
G = MAT["G_MPa"]


class Boom:
    """3-chord triangular lattice boom of circumradius R (mm)."""

    n_chord = 3

    def __init__(self, R: float, bay: float = BAY):
        self.R = float(R)
        self.bay = float(bay)
        self.batten = R * math.sqrt(3.0)                      # triangle side
        self.diagonal = math.hypot(self.batten, self.bay)

    # --- equivalent beam section -------------------------------------
    @property
    def A(self) -> float:
        """Axial area: chords only."""
        return self.n_chord * A_ROD

    @property
    def I(self) -> float:
        """Second moment. 3 chords on a circle R -> 1.5*A*R^2 about ANY
        centroidal axis (3-fold symmetry makes the inertia tensor isotropic).
        Chord self-inertia added for completeness."""
        return 1.5 * A_ROD * self.R ** 2 + self.n_chord * I_ROD

    @property
    def J_solid_equiv(self) -> float:
        """Upper bound only - what J would be if the lacing were a closed
        shear-carrying skin. Never use this for design; see J_calibrated."""
        return 2.0 * self.I

    def rod_length_per_m(self) -> float:
        """Total rod (mm) per 1000 mm of boom."""
        bays = 1000.0 / self.bay
        chords = self.n_chord * 1000.0
        lacing = bays * self.n_chord * (self.batten + self.diagonal)
        return chords + lacing

    def nodes_per_m(self) -> float:
        return (1000.0 / self.bay) * self.n_chord

    def mass_per_m_kg(self) -> float:
        rod_mm3 = self.rod_length_per_m() * A_ROD
        ball_r = PR["node_ball_dia_mm"] / 2.0
        ball_mm3 = self.nodes_per_m() * (4.0 / 3.0) * math.pi * ball_r ** 3
        return (rod_mm3 + ball_mm3) / 1000.0 * RHO / 1000.0

    def summary(self) -> dict:
        return {
            "R_mm": self.R, "bay_mm": self.bay,
            "batten_mm": round(self.batten, 1),
            "diagonal_mm": round(self.diagonal, 1),
            "A_mm2": round(self.A, 2),
            "I_mm4": round(self.I, 1),
            "rod_m_per_m": round(self.rod_length_per_m() / 1000.0, 2),
            "mass_kg_per_m": round(self.mass_per_m_kg(), 3),
        }


# ---------------------------------------------------------------------
# geometry of the micro rods, used both by the calibration and the builder
# ---------------------------------------------------------------------
def boom_rods(p0, p1, R: float, bay: float = BAY, phase: float = 0.0):
    """Explicit rod list for a boom running p0 -> p1.

    Returns (nodes, rods) where nodes is a list of (x,y,z) and rods is a list
    of (i, j, kind). Chords run the full length in `bay`-long segments so no
    rod ever exceeds the buckling limit.
    """
    import numpy as np

    p0 = np.asarray(p0, float)
    p1 = np.asarray(p1, float)
    axis = p1 - p0
    L = float(np.linalg.norm(axis))
    if L < 1e-6:
        return [], []
    ez = axis / L

    # stable perpendicular frame
    ref = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(ref, ez))) > 0.95:
        ref = np.array([1.0, 0.0, 0.0])
    ex = np.cross(ref, ez)
    ex /= np.linalg.norm(ex)
    ey = np.cross(ez, ex)

    # ceil, not round: rounding DOWN lets an 80 mm member become one
    # 80 mm bay, whose diagonal is 99 mm - well past the buckling limit.
    nbay = max(1, int(math.ceil(L / bay - 1e-9)))
    step = L / nbay

    nodes, rods = [], []
    ring_idx = []
    for s in range(nbay + 1):
        base = p0 + ez * (step * s)
        ring = []
        for c in range(3):
            a = phase + 2.0 * math.pi * c / 3.0
            pt = base + (ex * math.cos(a) + ey * math.sin(a)) * R
            ring.append(len(nodes))
            nodes.append(tuple(pt))
        ring_idx.append(ring)

    for s in range(nbay):
        lo, hi = ring_idx[s], ring_idx[s + 1]
        for c in range(3):
            rods.append((lo[c], hi[c], "chord"))
            rods.append((lo[c], lo[(c + 1) % 3], "batten"))
            rods.append((lo[c], hi[(c + 1) % 3], "diagonal"))
    for c in range(3):  # closing batten ring
        rods.append((ring_idx[-1][c], ring_idx[-1][(c + 1) % 3], "batten"))

    return nodes, rods


# ---------------------------------------------------------------------
def calibrate_J(R: float, length: float = 600.0) -> dict:
    """Solve one cantilevered boom in torsion with real rods, then back out the
    equivalent J that an beam element would need to match it."""
    import numpy as np
    from Pynite import FEModel3D

    nodes, rods = boom_rods((0, 0, 0), (length, 0, 0), R)
    m = FEModel3D()
    m.add_material("rod", E, G, MAT["poisson"], RHO * 1000.0 / 1e9)
    for i, (x, y, z) in enumerate(nodes):
        m.add_node(f"N{i}", x, y, z)
    m.add_section("rodsec", A_ROD, I_ROD, I_ROD, 2.0 * I_ROD)
    for k, (i, j, _) in enumerate(rods):
        m.add_member(f"M{k}", f"N{i}", f"N{j}", "rod", "rodsec")

    root_ring = [i for i, p in enumerate(nodes) if abs(p[0]) < 1e-6]
    tip_ring = [i for i, p in enumerate(nodes) if abs(p[0] - length) < 1e-6]
    for i in root_ring:
        m.def_support(f"N{i}", True, True, True, True, True, True)

    # torque applied as a force couple on the tip ring, tangentially
    T = 50_000.0  # N.mm
    F = T / (3.0 * R)
    for i in tip_ring:
        _, y, z = nodes[i]
        r = math.hypot(y, z)
        if r < 1e-9:
            continue
        m.add_node_load(f"N{i}", "FY", -F * (z / r), "T")
        m.add_node_load(f"N{i}", "FZ", F * (y / r), "T")

    m.add_load_combo("T", {"T": 1.0})
    m.analyze_linear(check_statics=False)

    twist = []
    for i in tip_ring:
        n = m.nodes[f"N{i}"]
        _, y, z = nodes[i]
        r = math.hypot(y, z)
        dy, dz = n.DY["T"], n.DZ["T"]
        twist.append((-dy * z + dz * y) / (r * r))   # rad
    theta = float(np.mean(twist))
    GJ = T * length / theta                    # N.mm^2
    J_eq = GJ / G
    return {"R_mm": R, "theta_rad": theta, "GJ_Nmm2": GJ, "J_eq_mm4": J_eq}


def main():
    print(f"rod {D_ROD} mm  A={A_ROD:.3f} mm2  bay={BAY:.0f} mm\n")
    print(f"{'R':>5} {'batten':>7} {'diag':>7} {'A':>7} {'I':>10} "
          f"{'rod m/m':>8} {'kg/m':>7}")
    for R in (20, 25, 30, 35, 40, 45):
        b = Boom(R)
        s = b.summary()
        print(f"{R:>5} {s['batten_mm']:>7.1f} {s['diagonal_mm']:>7.1f} "
              f"{s['A_mm2']:>7.2f} {s['I_mm4']:>10.0f} "
              f"{s['rod_m_per_m']:>8.2f} {s['mass_kg_per_m']:>7.3f}")

    print("\n--- benchmark: what steel tube has the same I? ---")
    for R in (25, 30, 35, 40):
        b = Boom(R)
        # 1.6 mm wall tube of outer dia d matching I
        best = None
        for d in [x / 10.0 for x in range(150, 600)]:
            t = 1.6
            Itube = math.pi * (d ** 4 - (d - 2 * t) ** 4) / 64.0
            if Itube >= b.I:
                Atube = math.pi * (d ** 2 - (d - 2 * t) ** 2) / 4.0
                best = (d, Atube * RHO / 1000.0)  # kg/m
                break
        if best:
            d, kg = best
            print(f"  R={R:>2} boom  I={b.I:>8.0f}  {b.mass_per_m_kg():.3f} kg/m"
                  f"   vs  {d:.1f}x1.6 tube {kg:.3f} kg/m"
                  f"   -> {(1 - b.mass_per_m_kg()/kg)*100:>4.0f}% lighter")

    print("\n--- torsion calibration (real rods, Pynite) ---")
    out = {}
    # 45-60 added 2026-10-06 (ACES) so larger booms interpolate instead of clamping at R=40
    for R in (25, 30, 35, 40, 45, 50, 55, 60):
        c = calibrate_J(R)
        b = Boom(R)
        ratio = c["J_eq_mm4"] / b.J_solid_equiv
        out[str(R)] = c
        print(f"  R={R:>2}  J_eq = {c['J_eq_mm4']:>9.0f} mm4   "
              f"({ratio:>5.1%} of a closed section of the same I)")

    (ROOT / P["output"]["dir"]).mkdir(parents=True, exist_ok=True)
    (ROOT / P["output"]["dir"] / "boom_calibration.json").write_text(
        json.dumps(out, indent=2))
    print("\nwrote out/boom_calibration.json")


if __name__ == "__main__":
    main()
