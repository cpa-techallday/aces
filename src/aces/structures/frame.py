"""Static check of strut lattices (space frames) with Pynite (MIT licence).

Every strut is a solid circular rod of the printed bead or rod diameter, rigidly
joined at nodes, in consistent units: mm, N, MPa.  For each load case the check
reports, per strut:

  stress     von Mises of (|N|/A + amplified bending M c/I) and torsion T c/J,
             against strength / material_factor
  buckling   compression N against the column capacity: Euler pi^2 E I/(K L)^2 for
             slender struts, Johnson's parabola in the inelastic range (whichever is
             lower), / material_factor; combined linearly with the bending ratio
  P-delta    bending in compressed struts amplified by 1 / (1 - N / N_euler)

and the governing utilisation over all struts and cases (feasible when <= 1).

Load cases are what the spec says, already factored by the engineer: self-weight
(a multiple of g along a direction), a uniform pressure on the area projected
normal to the pressure direction (distributed by tributary area, so the result
does not depend on mesh density; the area is the convex hull of the loaded nodes,
so openings and notches count as loaded, which is conservative), and point loads at the nearest node within a
snap tolerance.  Nothing here picks a load, a code clause or a safety factor on the
user's behalf.

Not checked: global (system) buckling, dynamic response, fatigue, joint strength,
shear deformation (matters below L/d ~ 8) and loads applied between nodes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, model_validator

G_ACC = 9.80665
NOT_CHECKED = ("global (system) buckling", "dynamic response and fatigue", "joint and weld strength",
               "shear deformation", "loads between nodes")


class FrameMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    E_MPa: PositiveFloat
    poisson: float = Field(gt=0.0, lt=0.5)
    strength_MPa: PositiveFloat = Field(description="characteristic (yield) strength used for stress and Johnson buckling")
    source: str = Field(min_length=3, description="datasheet or test report for E, poisson and strength")


class Supports(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["face", "points"]
    axis: Literal["x", "y", "z"] | None = None  # face mode
    side: Literal["min", "max"] | None = None  # face mode
    tol_mm: PositiveFloat = 5.0  # face mode: nodes within tol of the extreme coordinate
    points_mm: list[tuple[float, float, float]] = Field(default_factory=list)  # points mode: nearest nodes
    snap_tol_mm: PositiveFloat = 25.0  # points mode: a point must lie this close to a node
    restrain_rotation: bool = False  # True = clamped (moment-resisting fixings); False = pinned

    @model_validator(mode="after")
    def _complete(self):
        if self.mode == "face" and (self.axis is None or self.side is None):
            raise ValueError("face supports need axis and side")
        if self.mode == "points" and len(self.points_mm) < 3:
            raise ValueError("points supports need at least 3 points to restrain a rigid body")
        return self


class PointLoad(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_mm: tuple[float, float, float]
    force_N: tuple[float, float, float]
    snap_tol_mm: PositiveFloat = 25.0


class LoadCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    self_weight_g: float = 0.0  # multiple of g; 1.0 = unfactored self-weight
    gravity_direction: tuple[float, float, float] = (0.0, 0.0, -1.0)
    pressure_Pa: float = 0.0  # uniform pressure on the area projected normal to pressure_direction
    pressure_direction: tuple[float, float, float] = (0.0, 1.0, 0.0)
    point_loads: list[PointLoad] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_load(self):
        if not (self.self_weight_g or self.pressure_Pa or self.point_loads):
            raise ValueError(f"load case {self.name!r} applies no load")
        return self


class StructureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material: FrameMaterial
    material_factor: float = Field(ge=1.0, description="partial factor on strength and buckling (design choice)")
    effective_length_factor: float = Field(1.0, ge=0.5, le=2.0, description="K for strut buckling; 1.0 = pinned ends")
    supports: Supports
    load_cases: list[LoadCase] = Field(min_length=1)
    max_deflection_mm: PositiveFloat | None = None

    @model_validator(mode="after")
    def _unique_cases(self):
        names = [lc.name for lc in self.load_cases]
        if len(names) != len(set(names)):
            raise ValueError(f"load case names must be unique, got {names}")
        return self


@dataclass
class FrameResult:
    max_utilisation: float
    governing: dict
    over_unity: int
    max_deflection_mm: float
    deflection_case: str
    reactions_N: dict[str, list[float]]
    per_case: dict[str, dict]
    n_nodes: int
    n_struts: int
    support_nodes: int
    notes: list[str] = field(default_factory=list)
    not_checked: tuple[str, ...] = NOT_CHECKED

    def as_dict(self) -> dict:
        return self.__dict__.copy()


_SIGN: list[float] = []


def compression_sign() -> float:
    """+1.0 if Pynite reports compression as positive axial force, -1.0 if negative.

    Measured once on a one-bar model rather than assumed: PyNiteFEA 3.2 reports
    tension as negative, and code that assumed the opposite convention checked
    compressed struts against tension capacity.
    """
    if not _SIGN:
        from Pynite import FEModel3D

        m = FEModel3D()
        m.add_material("s", 200000.0, 77000.0, 0.3, 7.85e-9)
        m.add_section("r", 100.0, 1000.0, 1000.0, 2000.0)
        m.add_node("A", 0, 0, 0)
        m.add_node("B", 1000, 0, 0)
        m.add_member("M", "A", "B", "s", "r")
        m.def_support("A", True, True, True, True, True, True)
        m.add_node_load("B", "FX", -1000.0, "C")  # pushes B towards A: compression
        m.add_load_combo("C", {"C": 1.0})
        m.analyze_linear(check_statics=False)
        v = m.members["M"].max_axial("C")
        if not math.isclose(abs(v), 1000.0, rel_tol=1e-6):
            raise RuntimeError(f"Pynite sign probe returned {v}, expected +/-1000; cannot trust axial forces")
        _SIGN.append(1.0 if v > 0 else -1.0)
    return _SIGN[0]


def column_capacity_N(A: float, I: float, L: float, K: float, E: float, fy: float) -> tuple[float, float]:
    """(capacity, euler) for a pin-ended-equivalent column: min(Euler, Johnson parabola)."""
    r = math.sqrt(I / A)
    slender = K * L / r
    euler = math.pi**2 * E * I / max(K * L, 1e-9) ** 2
    cc = math.sqrt(2.0 * math.pi**2 * E / fy)  # Johnson / Euler transition slenderness
    if slender < cc:
        johnson = A * fy * (1.0 - fy * slender**2 / (4.0 * math.pi**2 * E))
        return min(euler, johnson), euler
    return euler, euler


def _unit(v) -> np.ndarray:
    a = np.asarray(v, float)
    n = np.linalg.norm(a)
    if not np.isfinite(n) or n == 0:
        raise ValueError("direction vector must be finite and non-zero")
    return a / n


def _nearest(nodes: np.ndarray, p, tol: float, what: str) -> int:
    dist = np.linalg.norm(nodes - np.asarray(p, float), axis=1)
    k = int(np.argmin(dist))
    if dist[k] > tol:
        raise ValueError(f"{what} at {list(p)} is {dist[k]:.1f} mm from the nearest node (tolerance {tol:g} mm)")
    return k


def _support_nodes(nodes: np.ndarray, s: Supports) -> list[int]:
    if s.mode == "face":
        ax = "xyz".index(s.axis)
        c = nodes[:, ax]
        ref = c.min() if s.side == "min" else c.max()
        idx = np.where(np.abs(c - ref) <= s.tol_mm)[0]
    else:
        idx = np.unique([_nearest(nodes, p, s.snap_tol_mm, "support point") for p in s.points_mm])
    if len(idx) < 3:
        raise ValueError(f"only {len(idx)} support node(s) found; need at least 3 non-collinear")
    return [int(i) for i in idx]


def _plane_basis(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    u = np.cross(direction, [1.0, 0.0, 0.0])
    if np.linalg.norm(u) < 1e-6:
        u = np.cross(direction, [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    return u, np.cross(direction, u)


def tributary_areas(points: np.ndarray, direction: np.ndarray) -> np.ndarray:
    """Area (mm2) each point carries of the region projected normal to ``direction``.

    Delaunay triangles of the projected points tile their convex hull exactly; each
    triangle's area goes one third to each corner.
    """
    from scipy.spatial import Delaunay
    from scipy.spatial._qhull import QhullError

    u, v = _plane_basis(direction)
    p2 = np.stack([points @ u, points @ v], axis=1)
    flat = ValueError("the loaded nodes project to a line or a point in this pressure direction "
                      "(is the direction in the plane of the panel?)")
    if len(p2) < 3:
        raise flat
    # Rank test before triangulating: joggling (QJ) would turn a line into slivers of ~zero area and the
    # pressure would silently vanish.
    sv = np.linalg.svd(p2 - p2.mean(axis=0), compute_uv=False)
    if sv[1] <= 1e-9 * max(sv[0], 1e-12):
        raise flat
    try:
        tri = Delaunay(p2)  # coincident projections are left out of the tiling; the hull area is unchanged
    except QhullError as exc:
        raise flat from exc
    a = p2[tri.simplices]
    area = 0.5 * np.abs((a[:, 1, 0] - a[:, 0, 0]) * (a[:, 2, 1] - a[:, 0, 1])
                        - (a[:, 2, 0] - a[:, 0, 0]) * (a[:, 1, 1] - a[:, 0, 1]))
    out = np.zeros(len(points))
    np.add.at(out, tri.simplices.ravel(), np.repeat(area / 3.0, 3))
    if not out.sum() > 0:
        raise flat
    return out


def analyse(
    nodes_mm,
    struts: list[tuple[int, int]],
    diameter_mm: float,
    density_kg_m3: float,
    spec: StructureSpec,
    loaded_nodes: list[int] | None = None,
) -> FrameResult:
    """Run every load case and return the governing utilisation.

    loaded_nodes: nodes that receive pressure loads (e.g. the outer skin); all nodes if None.
    """
    from Pynite import FEModel3D

    if not (diameter_mm > 0 and math.isfinite(diameter_mm)):
        raise ValueError(f"strut diameter must be positive, got {diameter_mm}")
    if not (density_kg_m3 > 0 and math.isfinite(density_kg_m3)):
        raise ValueError(f"density must be positive, got {density_kg_m3}")
    nodes = np.asarray(nodes_mm, float)
    if not np.isfinite(nodes).all():
        raise ValueError("node coordinates contain non-finite values")
    st, seen, dup = [], set(), 0
    for i, j in struts:
        i, j = int(i), int(j)
        if i == j:
            continue
        key = (min(i, j), max(i, j))
        if key in seen:  # a repeated strut would act as a second parallel member and halve its utilisation
            dup += 1
            continue
        seen.add(key)
        st.append((i, j))
    if not st:
        raise ValueError("lattice has no struts")
    mat = spec.material
    E, nu, fy = mat.E_MPa, mat.poisson, mat.strength_MPa
    G = E / (2.0 * (1.0 + nu))
    d = diameter_mm
    A = math.pi * d * d / 4.0
    I = math.pi * d**4 / 64.0
    J = 2.0 * I
    c = d / 2.0
    f_d = fy / spec.material_factor
    notes = [f"{dup} repeated strut(s) merged (listed more than once in the lattice)"] if dup else []

    m = FEModel3D()
    m.add_material("mat", E, G, nu, density_kg_m3 * 1e-9)
    m.add_section("rod", A, I, I, J)
    used = sorted({i for s in st for i in s})
    for i in used:
        x, y, z = nodes[i]
        m.add_node(f"N{i}", float(x), float(y), float(z))
    lengths = []
    for k, (i, j) in enumerate(st):
        m.add_member(f"M{k}", f"N{i}", f"N{j}", "mat", "rod")
        lengths.append(float(np.linalg.norm(nodes[i] - nodes[j])))
    lengths = np.asarray(lengths)
    if (lengths <= 0).any():
        raise ValueError("lattice has coincident nodes joined by a zero-length strut")

    used_arr = np.asarray(used)
    sup = [int(used_arr[i]) for i in _support_nodes(nodes[used_arr], spec.supports)]
    if not spec.supports.restrain_rotation:
        pts = nodes[sup] - nodes[sup].mean(axis=0)
        sv = np.linalg.svd(pts, compute_uv=False)
        if sv[1] <= 1e-6 * max(sv[0], 1e-12):
            raise ValueError("pinned support nodes are collinear, so the lattice can rotate about that line; "
                             "use non-collinear supports or restrain_rotation: true")
    rot = spec.supports.restrain_rotation
    for i in sup:
        m.def_support(f"N{i}", True, True, True, rot, rot, rot)

    # Lumped self-weight: half of each strut's mass to each end node.
    node_mass = np.zeros(len(nodes))
    strut_mass = A * lengths * density_kg_m3 * 1e-9  # kg
    for (i, j), mk in zip(st, strut_mass):
        node_mass[i] += mk / 2.0
        node_mass[j] += mk / 2.0
    loaded = sorted(set(loaded_nodes if loaded_nodes is not None else used) & set(used))

    applied: dict[str, np.ndarray] = {}
    for lc in spec.load_cases:
        F = np.zeros((len(nodes), 3))
        if lc.self_weight_g:
            F += np.outer(node_mass * G_ACC * lc.self_weight_g, _unit(lc.gravity_direction))
        if lc.pressure_Pa:
            dvec = _unit(lc.pressure_direction)
            trib = tributary_areas(nodes[loaded], dvec)
            F[loaded] += np.outer(lc.pressure_Pa * 1e-6 * trib, dvec)  # N/mm2 x mm2
            notes.append(f"{lc.name}: {lc.pressure_Pa:g} Pa on {trib.sum() / 1e6:.3f} m2 projected normal to "
                         f"{list(np.round(dvec, 3))}, {lc.pressure_Pa * 1e-6 * trib.sum():.0f} N by tributary "
                         f"area over {len(loaded)} nodes (convex hull: openings count as loaded)")
        for pl in lc.point_loads:
            k = _nearest(nodes[used_arr], pl.at_mm, pl.snap_tol_mm, f"point load in {lc.name}")
            F[used[k]] += np.asarray(pl.force_N, float)
        if not np.isfinite(F).all():
            raise ValueError(f"load case {lc.name} produced non-finite loads")
        applied[lc.name] = F.sum(axis=0)
        for i in used:
            for ax, name in enumerate(("FX", "FY", "FZ")):
                if F[i, ax] != 0.0:
                    m.add_node_load(f"N{i}", name, float(F[i, ax]), lc.name)
        m.add_load_combo(lc.name, {lc.name: 1.0})

    try:
        m.analyze_linear(check_statics=False, sparse=True)
    except Exception as exc:  # Pynite raises on singular stiffness (mechanisms)
        raise ValueError(f"structural model could not be solved (mechanism or unrestrained part?): {exc}") from exc

    sgn = compression_sign()
    K = spec.effective_length_factor
    caps = [column_capacity_N(A, I, L, K, E, fy) for L in lengths]
    best = {"u": 0.0}
    over = 0
    per_case: dict[str, dict] = {}
    max_defl, defl_case = 0.0, ""
    for lc in spec.load_cases:
        cb = lc.name
        u_case = 0.0
        for k in range(len(st)):
            mm = m.members[f"M{k}"]
            nmax, nmin = mm.max_axial(cb), mm.min_axial(cb)
            N = max(abs(nmax), abs(nmin))
            Mr = math.hypot(max(abs(mm.max_moment("My", cb)), abs(mm.min_moment("My", cb))),
                            max(abs(mm.max_moment("Mz", cb)), abs(mm.min_moment("Mz", cb))))
            T = max(abs(mm.max_torque(cb)), abs(mm.min_torque(cb)))
            comp = max(sgn * nmax, sgn * nmin, 0.0)  # compressive force, whatever Pynite's sign convention
            cap, euler = caps[k]
            amp = 1.0
            if comp > 0:
                amp = math.inf if comp >= euler else 1.0 / (1.0 - comp / euler)
            sb = Mr * c / I * amp
            tau = T * c / J
            u_stress = math.sqrt((N / A + sb) ** 2 + 3.0 * tau**2) / f_d
            u = u_stress
            if comp > 0:
                u = max(u, comp / (cap / spec.material_factor) + math.sqrt(sb**2 + 3.0 * tau**2) / f_d)
            if not math.isfinite(u):
                u = math.inf
            u_case = max(u_case, u)
            if u > 1.0:
                over += 1
            if u > best["u"]:
                best = {"u": u, "case": cb, "strut": k, "nodes": list(st[k]), "length_mm": float(lengths[k]),
                        "axial_N_tension_positive": -sgn * (nmin if abs(nmin) >= abs(nmax) else nmax),
                        "moment_Nmm": Mr, "torque_Nmm": T, "moment_amplification": amp,
                        "column_capacity_N": cap, "euler_load_N": euler}
        dmax = 0.0
        for i in used:
            n = m.nodes[f"N{i}"]
            dmax = max(dmax, math.sqrt(n.DX[cb] ** 2 + n.DY[cb] ** 2 + n.DZ[cb] ** 2))
        if dmax > max_defl:
            max_defl, defl_case = dmax, cb
        per_case[cb] = {"max_utilisation": u_case, "max_deflection_mm": dmax}

    reactions = {}
    for lc in spec.load_cases:
        tot = np.zeros(3)
        for i in sup:
            n = m.nodes[f"N{i}"]
            tot += [n.RxnFX[lc.name], n.RxnFY[lc.name], n.RxnFZ[lc.name]]
        reactions[lc.name] = tot.tolist()
        scale = max(np.abs(applied[lc.name]).max(), 1e-9)
        if np.abs(tot + applied[lc.name]).max() > 1e-6 * scale + 1e-6:
            raise ValueError(f"load case {lc.name}: reactions {tot.tolist()} do not balance the applied "
                             f"loads {applied[lc.name].tolist()}")
    if K == 1.0:
        notes.append("buckling uses K = 1.0 (pinned ends): conservative for fused or welded nodes")
    return FrameResult(float(best["u"]), best, over, float(max_defl), defl_case, reactions, per_case,
                       len(used), len(st), len(sup), notes)
