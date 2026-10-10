"""aces golden: the conforming second discretisation of a verified part (VISION section 5, build spec section 12).

run_check() meshes the part's STL into quadratic tetrahedra (gmsh, C3D10 with straight edges: second-order linear),
maps the spec's supports and traction loads onto the mesh surface, solves the linear static problem with CalculiX ccx
(SPOOLES, or the iterative Cholesky solver above SPOOLES_MAX_DOF), parses ccx.dat and derives the outputs the voxel
solves report: mass from the tet mesh volume, compliance, umax, and the von Mises percentiles over the elements
outside the boundary-condition zones the voxel solves exclude. compare() sets the result beside the finest voxel solve
inside bands that were fixed before any run (bands()).

The meshing runs in a child process (`python -m aces_engineering.golden.conforming mesh --stl S --out D --size H
--threads N`) under MESH_TIMEOUT_S, so a gmsh hang or crash cannot take the caller with it. run_check() never raises
for a modelling failure: it always writes <out_dir>/conforming.json (aces-golden-conforming/1) and returns it. A status
in NOT_RUN means the check did not run; any status other than "ok" means it ran and failed. Every number in the record
comes from the mesh, the deck or ccx's own output, and recompute() re-derives the metrics from ccx.inp.gz, ccx.dat.gz
and the record's material and stress boxes alone.

Conventions. Nodes are numbered in sorted gmsh-tag order (deck node i+1 is row i of mesh.npz); C3D10 corner nodes 0-3
and midside nodes 4-9 on the edges (0,1), (1,2), (2,0), (0,3), (1,3), (2,3), placed by geometry, never by assuming
gmsh's own order. Boxes are in the part frame (mm), as in the spec. Standard library only at module level: numpy,
trimesh and gmsh are imported where they are used.

Repeatability. gmsh meshes with General.NumThreads = threads, and with more than one thread its meshes differ from run
to run (measured on DARKNESS, gmsh 4.15.2: three 4-thread meshes of the patch-test bar had 895, 939 and 905 nodes;
three 1-thread meshes were identical). The record keeps the mesh that was solved (mesh.npz, ccx.inp.gz), and the
audit re-derives every number from those files, never by meshing again."""
from __future__ import annotations

import argparse
import contextlib
import gzip
import importlib.metadata
import importlib.util
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from . import clean, dump_json, sha256_file, utc_now, write_text

SCHEMA = "aces-golden-conforming/1"
GMSH_PIN = "4.15.2"
CCX_PIN = "2.23"
WINDOW_FACTOR = 0.5
NORMAL_MIN = 0.7071067811865476
LOAD_COVERAGE_MIN = 0.90
REMESH_VOLUME_TOL = 0.005
EQUILIBRIUM_TOL = 1e-6
SPOOLES_MAX_DOF = 800_000
DOF_PER_UNIT = 37.0
MAX_DOF = {"desktop": 1_500_000, "rig": 3_000_000}
TIMEOUT_S = {"desktop": 900.0, "rig": 3000.0}
MESH_TIMEOUT_S = 600.0
NOT_RUN = ("gmsh-not-found", "ccx-not-found", "too-large", "mesh-timeout", "ccx-timeout")
FAILED = ("not-watertight", "multi-body", "mesh-failed", "invalid-mesh", "bc-mapping-failed", "ccx-failed",
          "parse-failed", "equilibrium", "error")

EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))      # C3D10 midside nodes 4..9, in this order
FACES = ((0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3))           # corner triples of a tet's four faces
OPPOSITE = (3, 2, 0, 1)                                        # the corner each face does not contain
MID = {frozenset(e): 4 + k for k, e in enumerate(EDGES)}
CASE_FILES = ("tetmesh.json", "mesh.npz", "mesh.log", "ccx.inp.gz", "ccx.dat.gz", "ccx.sta", "ccx.cvg", "ccx.log")
CCX_VERSION_ARG = "-v"   # ccx 2.23 prints "This is Version 2.23" and exits 201 (checked on the official binary)
ATTEMPTS = (("A", "remesh", 1), ("B", "remesh", 10), ("C", "discrete", 1), ("D", "discrete", 10))
CHILD_STATUSES = ("ok", "not-watertight", "multi-body", "mesh-failed", "invalid-mesh", "error")
AXES = "xyz"
_PLANE_TOL = 1e-9
_NODE_TOL = 1e-6
_EXCLUDE_TOL = 1e-9
_SICN_P = 1.0                                                   # p01_sicn: the 1st percentile


class MappingError(ValueError):
    """A support or load that cannot be put on the mesh surface (status bc-mapping-failed). `partial` holds the
    support and load records mapped up to the failure (conforming.json keeps them)."""

    def __init__(self, message: str, partial: dict | None = None):
        super().__init__(message)
        self.partial = partial if partial is not None else {"supports": [], "loads": []}


class ParseError(ValueError):
    """ccx.inp or ccx.dat does not hold what the deck asked for (status parse-failed)."""


# ------------------------------------------------------------------------------------------------- what can run here

def _ccx_path(ccx: str | None) -> tuple[str | None, str]:
    """(path or None, where it was looked for): the argument, else $ACES_CCX, else ccx on PATH."""
    for cand, src in ((ccx, "the ccx argument"), (os.environ.get("ACES_CCX"), "ACES_CCX")):
        if cand:
            if Path(cand).is_file():
                return str(cand), src
            return shutil.which(str(cand)), f"{src} ({cand})"
    return shutil.which("ccx"), "PATH"


def available(ccx: str | None = None) -> dict:
    """What the conforming check can use here, without importing gmsh: {"gmsh": version|None, "gmsh_reason", "ccx":
    path|None, "ccx_version", "ccx_sha256", "usable", "reason"}. ccx is `ccx`, else $ACES_CCX, else `ccx` on PATH; it is
    run once with its version option (10 s timeout) and its sha256 is taken of the resolved file."""
    out = {"gmsh": None, "gmsh_reason": None, "ccx": None, "ccx_version": None, "ccx_sha256": None, "usable": False,
           "reason": None}
    try:
        found = importlib.util.find_spec("gmsh") is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        out["gmsh_reason"] = f"gmsh is not installed in this Python (the aces-engineering lock pins gmsh=={GMSH_PIN})"
    else:
        try:
            out["gmsh"] = importlib.metadata.version("gmsh")
        except importlib.metadata.PackageNotFoundError:
            out["gmsh_reason"] = "the gmsh module has no distribution metadata (not the PyPI wheel)"
        else:
            out["gmsh_reason"] = (f"gmsh {out['gmsh']}" if out["gmsh"] == GMSH_PIN else
                                  f"gmsh {out['gmsh']} (the lock pins {GMSH_PIN}; the record names the version used)")
    path, src = _ccx_path(ccx)
    ccx_reason = None
    if path is None:
        ccx_reason = ("ccx not found (no ACES_CCX and no ccx on PATH; on ACES OS: sudo pacman -S calculix-ccx-bin)"
                      if src == "PATH" else f"ccx not found at {src}")
    else:
        real = os.path.realpath(path)
        out["ccx"] = path
        with contextlib.suppress(OSError):
            out["ccx_sha256"] = sha256_file(real)
        try:
            p = subprocess.run([path, CCX_VERSION_ARG], capture_output=True, text=True, errors="replace", timeout=10.0,
                               stdin=subprocess.DEVNULL)
            text = (p.stdout or "") + (p.stderr or "")
            m = re.search(r"Version\s+([0-9][0-9A-Za-z.]*)", text)
            if m:
                out["ccx_version"] = m.group(1)
            else:
                first = next((ln.strip() for ln in text.splitlines() if ln.strip()), f"exit {p.returncode}")
                ccx_reason = f"ccx at {path} does not run: {first[:200]}"
        except subprocess.TimeoutExpired:
            ccx_reason = f"ccx at {path} did not answer {CCX_VERSION_ARG} within 10 s"
        except OSError as exc:
            ccx_reason = f"ccx at {path} cannot be started: {exc}"
    out["usable"] = bool(out["gmsh"]) and out["ccx_version"] is not None
    if not out["gmsh"]:
        out["reason"] = out["gmsh_reason"] if ccx_reason is None else f"{out['gmsh_reason']}; {ccx_reason}"
    elif ccx_reason:
        out["reason"] = ccx_reason
    else:
        note = "" if out["ccx_version"] == CCX_PIN else f" (pinned {CCX_PIN})"
        out["reason"] = f"{out['gmsh_reason']} and CalculiX ccx {out['ccx_version']}{note} at {path}"
    return out


def estimate_dof(volume_mm3: float, size_mm: float) -> int:
    """Degrees of freedom of a tet10 mesh of this volume at this element size: DOF_PER_UNIT * V / h^3, rounded up."""
    if not (volume_mm3 > 0 and size_mm > 0):
        return 0
    return int(math.ceil(DOF_PER_UNIT * float(volume_mm3) / float(size_mm) ** 3))


def bc_from(eff: dict, domain: dict) -> dict:
    """What the mapping and the metrics need from the effective spec and the domain record: the supports and loads
    (named as the voxel solve names them), the material, the window (WINDOW_FACTOR * fea_mm), the grid envelope that
    decides which way a support or load face must face, and the stress-exclusion boxes of the voxel solves."""
    d = eff.get("derived") or {}
    grid = d.get("grid") or {}
    fea = d.get("fea_mm")
    if fea is None:
        fea = (eff.get("resolution") or {}).get("fea_mm") or (eff.get("resolution") or {}).get("gen_mm")
    fea = float(fea)
    sups, loads = [], []
    seen: set = set()
    for i, s in enumerate(eff.get("supports") or []):
        name = s.get("name") or f"support{i + 1}"
        if name in seen:
            name = f"{name}#{i + 1}"
        seen.add(name)
        sups.append({"name": name, "box": [float(v) for v in s["box"]],
                     "dofs": [int(x) for x in (s.get("dofs") if s.get("dofs") is not None else (0, 1, 2))]})
    seen = set()
    for i, ld in enumerate(eff.get("loads") or []):
        name = ld.get("name") or f"load{i + 1}"
        if name in seen:
            name = f"{name}#{i + 1}"
        seen.add(name)
        loads.append({"name": name, "box": [float(v) for v in ld["box"]], "force": [float(f) for f in ld["force"]],
                      "axis": ld.get("axis"), "case": ld.get("case", "default")})
    mat = eff.get("material") or {}
    return {"supports": sups, "loads": loads,
            "material": {"E_MPa": float(mat["E_MPa"]), "nu": float(mat["nu"]), "rho_kg_mm3": float(mat["rho_kg_mm3"])},
            "fea_mm": fea, "window_mm": WINDOW_FACTOR * fea, "normal_min": NORMAL_MIN,
            "origin_mm": [float(v) for v in grid["origin_mm"]] if grid.get("origin_mm") is not None else None,
            "envelope_mm": [float(v) for v in grid["envelope_mm"]] if grid.get("envelope_mm") is not None else None,
            "exclude_boxes": [[float(v) for v in b] for b in (domain or {}).get("stress_exclude") or []]}


# ------------------------------------------------------------------------------------------------ the STL, cleaned

def _load_stl(path):
    """The part's STL loaded with trimesh (merged vertices), degenerate faces removed, winding made consistent per
    component and the whole mesh turned outward when its signed volume is negative. Returns (mesh, components, info):
    components are (submesh, signed volume) with the shells (positive) first, largest first, then the cavities."""
    import numpy as np
    import trimesh

    m = trimesh.load(str(path), force="mesh", process=True)
    info = {"faces": int(len(m.faces)), "vertices": int(len(m.vertices)), "watertight": bool(m.is_watertight),
            "volume_mm3": None, "area_mm2": float(m.area), "shells": None, "cavities": None,
            "bbox_mm": [float(v) for v in np.asarray(m.bounds, dtype=np.float64).reshape(-1)] if len(m.faces) else None}
    if not info["watertight"]:
        return m, [], info
    keep = m.nondegenerate_faces()
    if not bool(np.all(keep)):
        m.update_faces(keep)
        m.remove_unreferenced_vertices()
    trimesh.repair.fix_winding(m)
    if float(m.volume) < 0:
        m.invert()
    comps = []
    for c in m.split(only_watertight=False):
        comps.append((c, float(c.volume)))
    shells = sorted([c for c in comps if c[1] > 0], key=lambda t: -t[1])
    cavities = sorted([c for c in comps if c[1] <= 0], key=lambda t: t[1])
    info.update(volume_mm3=float(m.volume), watertight=bool(m.is_watertight), shells=len(shells),
                cavities=len(cavities))
    return m, shells + cavities, info


# --------------------------------------------------------------------------------------------- C3D10 from gmsh tets

def _det(a, b, c):
    import numpy as np

    return np.einsum("ij,ij->i", a, np.cross(b, c))


def order_tet10(nodes, raw, *, chunk: int = 200_000):
    """C3D10 connectivity (M, 10) from 10-node tets in any midside order: the corners as given, corners 1 and 2 swapped
    where det[X1-X0, X2-X0, X3-X0] < 0, and the six midside nodes placed on the edges (0,1), (1,2), (2,0), (0,3), (1,3),
    (2,3) by matching each edge midpoint within 1e-6 x the edge length. The match must be a permutation of the six
    midside nodes; a degenerate corner tet or a failed match raises ValueError."""
    import numpy as np

    X = np.asarray(nodes, dtype=np.float64)
    raw = np.asarray(raw, dtype=np.int64)
    if raw.ndim != 2 or raw.shape[1] != 10:
        raise ValueError(f"expected (M, 10) tet10 connectivity, got {raw.shape}")
    out = np.empty_like(raw)
    ea = [e[0] for e in EDGES]
    eb = [e[1] for e in EDGES]
    for s in range(0, len(raw), chunk):
        r = raw[s:s + chunk]
        c = r[:, :4].copy()
        d = _det(X[c[:, 1]] - X[c[:, 0]], X[c[:, 2]] - X[c[:, 0]], X[c[:, 3]] - X[c[:, 0]])
        flip = d < 0
        c[flip, 1], c[flip, 2] = r[flip, 2], r[flip, 1]
        bad = ~(np.abs(d) > 0)
        if bad.any():
            raise ValueError(f"{int(bad.sum())} tetrahedra have zero volume (first at element {s + int(np.argmax(bad))})")
        mids = r[:, 4:10]
        A, B = X[c[:, ea]], X[c[:, eb]]
        target = 0.5 * (A + B)                                          # (m, 6, 3)
        length = np.linalg.norm(A - B, axis=2)                          # (m, 6)
        cand = X[mids]                                                  # (m, 6, 3)
        hit = np.empty((len(r), 6, 6), dtype=bool)
        for k in range(6):
            dist = np.linalg.norm(cand - target[:, k:k + 1, :], axis=2)  # (m, 6)
            hit[:, k, :] = dist <= 1e-6 * length[:, k:k + 1]
        if not (np.all(hit.sum(axis=2) == 1) and np.all(hit.sum(axis=1) == 1)):
            row = int(np.argmax(~(np.all(hit.sum(axis=2) == 1, axis=1) & np.all(hit.sum(axis=1) == 1, axis=1))))
            raise ValueError(f"the midside nodes of element {s + row} do not sit on its edge midpoints "
                             "(not a straight-sided tet10)")
        out[s:s + chunk, :4] = c
        out[s:s + chunk, 4:] = np.take_along_axis(mids, hit.argmax(axis=2), axis=1)
    return out


def tet_volumes(nodes, tets):
    """Element volumes from the corner coordinates (exact for straight-sided tet10)."""
    import numpy as np

    X = np.asarray(nodes, dtype=np.float64)
    T = np.asarray(tets, dtype=np.int64)
    return np.abs(_det(X[T[:, 1]] - X[T[:, 0]], X[T[:, 2]] - X[T[:, 0]], X[T[:, 3]] - X[T[:, 0]])) / 6.0


def boundary_faces(nodes, tets) -> dict:
    """The 6-node boundary faces of a C3D10 mesh: the corner triples that belong to one element only, oriented outward
    (the normal points away from the element's fourth corner). Returns {"tri": (F, 3) corners, "mid": (F, 3) midside
    nodes on the edges (a,b), (b,c), (c,a), "elem": (F,), "area": (F,), "normal": (F, 3) unit, "centroid": (F, 3)}."""
    import numpy as np

    X = np.asarray(nodes, dtype=np.float64)
    T = np.asarray(tets, dtype=np.int64)
    M = len(T)
    loc = np.array(FACES, dtype=np.int64)                                # (4, 3) local corners
    tri = T[:, loc].reshape(-1, 3)                                       # (4M, 3), face f of element e at 4e + f
    key = np.sort(tri, axis=1)
    n = int(len(X))
    if n ** 3 < 2 ** 62:
        flat = (key[:, 0] * n + key[:, 1]) * n + key[:, 2]
        _, inv, cnt = np.unique(flat, return_inverse=True, return_counts=True)
    else:
        _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    once = np.flatnonzero(cnt[inv.reshape(-1)] == 1)
    elem = once // 4
    fidx = once % 4
    la = loc[fidx, 0]
    lb = loc[fidx, 1]
    lc = loc[fidx, 2]
    a, b, c = T[elem, la], T[elem, lb], T[elem, lc]
    opp = T[elem, np.array(OPPOSITE, dtype=np.int64)[fidx]]
    nrm = np.cross(X[b] - X[a], X[c] - X[a])
    flip = np.einsum("ij,ij->i", nrm, X[opp] - X[a]) > 0
    lb2 = np.where(flip, lc, lb)
    lc2 = np.where(flip, lb, lc)
    b2 = np.where(flip, c, b)
    c2 = np.where(flip, b, c)
    nrm = np.where(flip[:, None], -nrm, nrm)
    mid_of = np.zeros((4, 4), dtype=np.int64)
    for e, k in MID.items():
        i, j = tuple(e)
        mid_of[i, j] = mid_of[j, i] = k
    mid = np.stack([T[elem, mid_of[la, lb2]], T[elem, mid_of[lb2, lc2]], T[elem, mid_of[lc2, la]]], axis=1)
    norm = np.linalg.norm(nrm, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        unit = nrm / norm[:, None]
    del M
    return {"tri": np.stack([a, b2, c2], axis=1), "mid": mid, "elem": elem, "area": 0.5 * norm, "normal": unit,
            "centroid": (X[a] + X[b2] + X[c2]) / 3.0}


# ------------------------------------------------------------------------------------- boundary conditions on a mesh

def _planar_axes(box) -> list[int]:
    return [a for a in range(3) if abs(float(box[a]) - float(box[a + 3])) <= _PLANE_TOL * max(1.0, abs(float(box[a])))]


def _faces_on_plane(bf: dict, box, a: int, window: float, origin, envelope):
    """Boundary faces of a planar box on axis a: centroid inside the in-plane extent (inclusive), centroid within the
    window of the plane, and facing the right way (outward -a at the envelope minimum, +a at the maximum, either way
    elsewhere, |n[a]| >= NORMAL_MIN)."""
    import numpy as np

    c, nrm = bf["centroid"], bf["normal"]
    p = float(box[a])
    sel = np.abs(c[:, a] - p) <= window
    for j in range(3):
        if j != a:
            sel &= (c[:, j] >= float(box[j])) & (c[:, j] <= float(box[j + 3]))
    lo = hi = None
    if origin is not None and envelope is not None:
        lo, hi = float(origin[a]), float(origin[a]) + float(envelope[a])
    if lo is not None and abs(p - lo) <= _PLANE_TOL * max(1.0, abs(lo)):
        sel &= nrm[:, a] <= -NORMAL_MIN
    elif hi is not None and abs(p - hi) <= _PLANE_TOL * max(1.0, abs(hi)):
        sel &= nrm[:, a] >= NORMAL_MIN
    else:
        sel &= np.abs(nrm[:, a]) >= NORMAL_MIN
    return sel


def _nodes_in_box(nodes, box):
    import numpy as np

    X = np.asarray(nodes)
    sel = np.ones(len(X), dtype=bool)
    for a in range(3):
        sel &= (X[:, a] >= float(box[a]) - _NODE_TOL) & (X[:, a] <= float(box[a + 3]) + _NODE_TOL)
    return np.flatnonzero(sel)


def _footprint(box, a: int) -> float:
    o = [j for j in range(3) if j != a]
    return float(box[o[0] + 3] - box[o[0]]) * float(box[o[1] + 3] - box[o[1]])


def map_bcs(nodes, tets, bc: dict, bf: dict | None = None) -> dict:
    """Supports and loads on the mesh (section 12.3). Returns {"supports": [{..., "node_ids"}], "loads": [{...}],
    "cload": (K, 3) rows (node, dof, value) of the non-zero nodal loads, "extra_dofs": [(nodes, dofs)]}; raises
    MappingError naming the support or load that cannot be mapped (its `partial` keeps the records mapped so far).

    A support whose box is planar on exactly one axis is the set of all nodes of the boundary faces selected on that
    plane (_faces_on_plane); any other support box selects the mesh nodes inside it (+-1e-6 mm). A node in two supports
    stays in the first support's node set, so each node's reaction is counted once in the support totals, and the dofs
    the later support fixes and the first does not are still fixed on it (extra_dofs), as the voxel solve fixes the
    union. A traction load (axis given) spreads F over the selected faces as the uniform traction F / sum(A_f), with
    the consistent tet10 face loads: 0 at the corners and t A_f / 3 at each midside node; its coverage (projected area
    over the box footprint) must reach LOAD_COVERAGE_MIN. A load without an axis is split equally over the nodes inside
    its box. A node that is both supported and loaded is refused when the load acts along a dof the support fixes (the
    load would go straight into the reaction); a load along a free dof of a supported node is kept, as on the edges the
    section 12.8 patch test shares between its symmetry supports and its loaded face."""
    import numpy as np

    X = np.asarray(nodes, dtype=np.float64)
    bf = bf if bf is not None else boundary_faces(X, tets)
    w = float(bc["window_mm"])
    origin, envelope = bc.get("origin_mm"), bc.get("envelope_mm")
    owner = np.full(len(X), -1, dtype=np.int64)
    fixed = np.zeros((len(X), 3), dtype=bool)
    sups: list[dict] = []
    loads: list[dict] = []
    extra: list = []
    done = {"supports": sups, "loads": loads}
    for i, s in enumerate(bc["supports"]):
        box = s["box"]
        planar = _planar_axes(box)
        rec = {"name": s["name"], "box": box, "dofs": list(s["dofs"]), "mode": None, "axis": None, "plane_mm": None,
               "faces": None, "nodes": 0, "area_mm2": None, "projected_mm2": None, "footprint_mm2": None,
               "coverage": None, "plane_offset_mm": None, "overlap_nodes": 0, "node_ids": np.zeros(0, dtype=np.int64)}
        sups.append(rec)
        if len(planar) == 1:
            a = planar[0]
            sel = _faces_on_plane(bf, box, a, w, origin, envelope)
            area = bf["area"][sel]
            proj = float(np.sum(area * np.abs(bf["normal"][sel, a])))
            foot = _footprint(box, a)
            rec.update(mode="surface", axis=AXES[a], plane_mm=float(box[a]), faces=int(sel.sum()),
                       area_mm2=float(area.sum()), projected_mm2=proj, footprint_mm2=foot,
                       coverage=(proj / foot) if foot > 0 else None,
                       plane_offset_mm=float(np.max(np.abs(bf["centroid"][sel, a] - float(box[a])))) if sel.any() else None)
            if not sel.any():
                raise MappingError(f"support {s['name']}: no surface of the part within {w:g} mm of "
                                   f"{AXES[a]}={float(box[a]):g} inside its box", done)
            ids = np.unique(np.concatenate([bf["tri"][sel].reshape(-1), bf["mid"][sel].reshape(-1)]))
        else:
            rec["mode"] = "nodes"
            ids = _nodes_in_box(X, box)
            if ids.size == 0:
                raise MappingError(f"support {s['name']}: no mesh node inside its box {[float(v) for v in box]}", done)
        for d in s["dofs"]:
            fixed[ids, int(d)] = True
        taken = ids[owner[ids] >= 0]
        if taken.size:
            rec["overlap_nodes"] = int(taken.size)
            for j in sorted(set(int(k) for k in owner[taken])):
                more = [d for d in s["dofs"] if d not in bc["supports"][j]["dofs"]]
                if more:
                    extra.append((taken[owner[taken] == j], more))
        own = ids[owner[ids] < 0]
        owner[own] = i
        rec["node_ids"] = own
        rec["nodes"] = int(own.size)
    load_vec = np.zeros((len(X), 3), dtype=np.float64)
    for ld in bc["loads"]:
        box = ld["box"]
        F = np.asarray(ld["force"], dtype=np.float64)
        rec = {"name": ld["name"], "box": box, "force": [float(f) for f in F], "axis": ld.get("axis"), "mode": None,
               "plane_mm": None, "faces": None, "nodes": 0, "area_mm2": None, "projected_mm2": None,
               "footprint_mm2": None, "coverage": None, "plane_offset_mm": None, "traction_MPa": None}
        loads.append(rec)
        if ld.get("axis"):
            a = AXES.index(ld["axis"])
            rec.update(mode="traction", plane_mm=float(box[a]))
            if a not in _planar_axes(box):
                raise MappingError(f"load {ld['name']}: its box is not planar on its axis {ld['axis']}", done)
            foot = _footprint(box, a)
            rec["footprint_mm2"] = foot
            if not foot > 0:
                raise MappingError(f"load {ld['name']}: its box has no footprint on the {ld['axis']} plane", done)
            sel = _faces_on_plane(bf, box, a, w, origin, envelope)
            if not sel.any():
                raise MappingError(f"load {ld['name']}: no surface of the part within {w:g} mm of "
                                   f"{ld['axis']}={float(box[a]):g} inside its box", done)
            area = bf["area"][sel]
            A = float(area.sum())
            proj = float(np.sum(area * np.abs(bf["normal"][sel, a])))
            cov = proj / foot
            rec.update(faces=int(sel.sum()), area_mm2=A, projected_mm2=proj, coverage=cov,
                       plane_offset_mm=float(np.max(np.abs(bf["centroid"][sel, a] - float(box[a])))))
            if cov < LOAD_COVERAGE_MIN:
                raise MappingError(f"load {ld['name']}: the selected surface covers {cov:.3f} of its {foot:g} mm2 "
                                   f"footprint (minimum {LOAD_COVERAGE_MIN:g})", done)
            t = F / A
            rec["traction_MPa"] = [float(v) for v in t]
            mids = bf["mid"][sel]                                              # (f, 3) midside nodes per face
            share = (area / 3.0)[:, None] * t[None, :]                         # (f, 3) force per midside node
            at = np.concatenate([mids[:, 0], mids[:, 1], mids[:, 2]])
            vals = np.concatenate([share, share, share])
        else:
            rec["mode"] = "nodal"
            at = _nodes_in_box(X, box)
            if at.size == 0:
                raise MappingError(f"load {ld['name']}: no mesh node inside its box {[float(v) for v in box]}", done)
            vals = np.repeat(F[None, :] / float(at.size), at.size, axis=0)
        bad = fixed[at] & (vals != 0.0)
        if bad.any():
            r, d = (int(v) for v in np.argwhere(bad)[0])
            k = int(at[r])
            who = sups[int(owner[k])]["name"] if owner[k] >= 0 else "a support"
            raise MappingError(f"node {k + 1} at ({X[k, 0]:g}, {X[k, 1]:g}, {X[k, 2]:g}) is loaded by {ld['name']} "
                               f"along {AXES[d]}, which support {who} fixes", done)
        np.add.at(load_vec, at, vals)
        rec["nodes"] = int(np.unique(at).size)
    nz = np.argwhere(load_vec != 0.0)
    cload = (np.column_stack([nz[:, 0].astype(np.float64), nz[:, 1].astype(np.float64), load_vec[nz[:, 0], nz[:, 1]]])
             if nz.size else np.zeros((0, 3)))
    return {"supports": sups, "loads": loads, "cload": cload, "extra_dofs": extra}


# ---------------------------------------------------------------------------------------------------------- the deck

def _ints(values, per_line: int = 10) -> list[str]:
    """Data lines of integers, 10 per line: ccx allows 16 entries and 132 characters per line, and 16 seven-digit node
    numbers would not fit."""
    vals = [str(int(v)) for v in values]
    return [", ".join(vals[i:i + per_line]) for i in range(0, len(vals), per_line)]


def _fnum(x: float) -> str:
    """A float for the deck: repr when it fits ccx's 20-character number field, else the longest E format that does
    (ccx reads every real with an F20.0 edit, so -5.148283889866434e-05, 22 characters, is refused; 14 significant
    digits fit, 13 with a three-digit exponent). read_results() reads the deck back, so the metrics use exactly
    the numbers ccx used."""
    x = float(x)
    s = repr(x)
    if len(s) <= 20:
        return s
    for p in range(16, 0, -1):
        s = f"{x:.{p}e}"
        if len(s) <= 20:
            return s
    raise ValueError(f"{x!r} does not fit a 20-character field")


def write_deck(path, nodes, tets, mapped: dict, material: dict, *, heading: str) -> dict:
    """ccx.inp (section 12.4): nodes NALL (coordinates by repr), C3D10 elements EALL, one NSET SUP_<i> per support,
    the material, a linear *STATIC step (SPOOLES, or ITERATIVE CHOLESKY above SPOOLES_MAX_DOF), *BOUNDARY, *CLOAD
    (non-zero components), U for NALL, RF totals per support, S for EALL. Returns {"dof", "linear_solver_card"}."""
    import numpy as np

    X = np.asarray(nodes, dtype=np.float64)
    T = np.asarray(tets, dtype=np.int64)
    dof = 3 * int(len(X))
    card = "SPOOLES" if dof <= SPOOLES_MAX_DOF else "ITERATIVE CHOLESKY"
    L = ["*HEADING", heading.replace("\n", " ")[:120], "*NODE, NSET=NALL"]
    xs = X.tolist()  # Python floats (numpy 2 writes a scalar as "np.float64(...)"), each in a 20-character field
    L += [f"{i + 1}, {_fnum(x)}, {_fnum(y)}, {_fnum(z)}" for i, (x, y, z) in enumerate(xs)]
    L.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    L += [f"{e + 1}, " + ", ".join(str(int(v) + 1) for v in T[e]) for e in range(len(T))]
    # a support whose every node is already in an earlier support's set has no set of its own: its reactions are in
    # that support's totals and its extra dofs are fixed node by node below
    own = [i for i, s in enumerate(mapped["supports"]) if len(s["node_ids"])]
    for i in own:
        L.append(f"*NSET, NSET=SUP_{i + 1}")
        L += _ints(np.asarray(mapped["supports"][i]["node_ids"], dtype=np.int64) + 1)
    L += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{_fnum(material['E_MPa'])}, {_fnum(material['nu'])}",
          "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT", "*STEP", f"*STATIC, SOLVER={card}", "*BOUNDARY"]
    for i in own:
        dofs = sorted(int(d) for d in mapped["supports"][i]["dofs"])
        if dofs == [0, 1, 2]:
            L.append(f"SUP_{i + 1}, 1, 3")
        else:
            L += [f"SUP_{i + 1}, {d + 1}, {d + 1}" for d in dofs]
    for ids, dofs in mapped.get("extra_dofs") or []:
        for n in np.asarray(ids, dtype=np.int64).tolist():
            L += [f"{n + 1}, {int(d) + 1}, {int(d) + 1}" for d in sorted(dofs)]
    L.append("*CLOAD")
    for n, d, v in np.asarray(mapped["cload"], dtype=np.float64).reshape(-1, 3).tolist():
        L.append(f"{int(n) + 1}, {int(d) + 1}, {_fnum(v)}")
    L += ["*NODE PRINT, NSET=NALL", "U"]
    for i in own:
        L += [f"*NODE PRINT, NSET=SUP_{i + 1}, TOTALS=ONLY", "RF"]
    L += ["*EL PRINT, ELSET=EALL", "S", "*END STEP"]
    write_text(path, "\n".join(L) + "\n")
    return {"dof": dof, "linear_solver_card": card}


# -------------------------------------------------------------------------------------------- reading ccx's results

_FLOAT = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[EeDd][-+]?\d+|[-+]\d{3})?")


def _f(token: str) -> float:
    """A Fortran E-format number: 1.234567E-03, 1.234567D-03, or 1.234567-100 (three-digit exponent without E)."""
    t = token.replace("D", "E").replace("d", "e")
    if "E" not in t and "e" not in t:
        t = re.sub(r"(?<=\d)([-+]\d{3})$", r"E\1", t)
    return float(t)


def _open_text(case_dir: Path, name: str) -> str:
    gz, plain = case_dir / f"{name}.gz", case_dir / name
    if gz.is_file():
        with gzip.open(gz, "rt", encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    if plain.is_file():
        return plain.read_text(encoding="utf-8", errors="replace")
    raise ParseError(f"no {name}.gz or {name} in {case_dir}")


def _read_deck(text: str) -> dict:
    import numpy as np

    nodes, elems, nsets, cload = {}, {}, {}, []
    section, name = None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            head = [p.strip().upper() for p in line.split(",")]
            kw = head[0]
            opts = dict(p.split("=", 1) if "=" in p else (p, "") for p in head[1:])
            section, name = kw, None
            if kw in ("*NSET", "*NODE") and opts.get("NSET"):
                name = opts["NSET"]
                nsets.setdefault(name, [])
            if kw == "*ELEMENT" and opts.get("TYPE") != "C3D10":
                raise ParseError(f"element type {opts.get('TYPE')} is not C3D10")
            continue
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if section == "*NODE":
            nodes[int(parts[0])] = [float(v) for v in parts[1:4]]
            if name:
                nsets[name].append(int(parts[0]))
        elif section == "*ELEMENT":
            elems[int(parts[0])] = [int(v) for v in parts[1:11]]
        elif section == "*NSET":
            nsets[name] += [int(v) for v in parts]
        elif section == "*CLOAD":
            cload.append((int(parts[0]), int(parts[1]), float(parts[2])))
    n, m = len(nodes), len(elems)
    if n == 0 or m == 0:
        raise ParseError("ccx.inp has no nodes or no elements")
    if sorted(nodes) != list(range(1, n + 1)) or sorted(elems) != list(range(1, m + 1)):
        raise ParseError("ccx.inp node or element numbers are not 1..N")
    X = np.array([nodes[i] for i in range(1, n + 1)], dtype=np.float64)
    T = np.array([elems[i] for i in range(1, m + 1)], dtype=np.int64) - 1
    if T.shape != (m, 10):
        raise ParseError("an element of ccx.inp does not have 10 nodes")
    sets = {k: np.array(sorted(set(v)), dtype=np.int64) - 1 for k, v in nsets.items()}
    cl = np.array([(a - 1, b - 1, c) for a, b, c in cload], dtype=np.float64).reshape(-1, 3)
    return {"nodes": X, "tets": T, "nsets": sets, "cload": cl}


_HEAD = re.compile(r"^\s*(displacements|stresses|total force)\s*\(([^)]*)\)\s*for set\s+(\S+)\s+and time\s+(\S+)",
                   re.IGNORECASE)


def _dat_blocks(text: str) -> list[tuple[str, str, list[str]]]:
    blocks: list = []
    cur = None
    for raw in text.splitlines():
        m = _HEAD.match(raw)
        if m:
            cur = (m.group(1).lower(), m.group(3).upper(), [])
            blocks.append(cur)
            continue
        if cur is not None:
            if raw.strip():
                cur[2].append(raw)
            elif cur[2]:
                cur = None
    return blocks


def _rows(lines: list[str], n_int: int, n_float: int, what: str):
    import numpy as np

    ints, flts = [], []
    for ln in lines:
        toks = ln.split()
        if len(toks) != n_int + n_float:
            toks = toks[:n_int] + _FLOAT.findall(" ".join(toks[n_int:]))
        if len(toks) != n_int + n_float:
            raise ParseError(f"{what}: unexpected line {ln.strip()[:80]!r}")
        try:
            ints.append([int(t) for t in toks[:n_int]])
            flts.append([_f(t) for t in toks[n_int:]])
        except ValueError:
            raise ParseError(f"{what}: unreadable number in {ln.strip()[:80]!r}") from None
    return (np.array(ints, dtype=np.int64).reshape(len(lines), n_int),
            np.array(flts, dtype=np.float64).reshape(len(lines), n_float))


def read_results(case_dir) -> dict:
    """ccx.inp(.gz) and ccx.dat(.gz) of a case directory: {"nodes": (N, 3), "tets": (M, 10) 0-based C3D10, "nsets":
    {name: 0-based node indices}, "cload": (K, 3) rows (node, dof, value), "u": (N, 3), "s_ip": (M, 4, 6) with
    sxx, syy, szz, sxy, sxz, syz per integration point, "rf": {set: (3,)}}. Raises ParseError on any inconsistency."""
    import numpy as np

    case_dir = Path(case_dir)
    deck = _read_deck(_open_text(case_dir, "ccx.inp"))
    n, m = len(deck["nodes"]), len(deck["tets"])
    u = s_ip = None
    rf = {}
    for kind, name, lines in _dat_blocks(_open_text(case_dir, "ccx.dat")):
        if kind == "displacements" and name == "NALL":
            ids, vals = _rows(lines, 1, 3, "displacements")
            if len(ids) != n or not np.array_equal(np.sort(ids[:, 0]), np.arange(1, n + 1)):
                raise ParseError(f"displacements for NALL list {len(ids)} nodes, the deck has {n}")
            u = np.empty((n, 3))
            u[ids[:, 0] - 1] = vals
        elif kind == "stresses" and name == "EALL":
            ids, vals = _rows(lines, 2, 6, "stresses")
            if len(ids) != 4 * m:
                raise ParseError(f"stresses for EALL list {len(ids)} integration points, the deck has {m} x 4")
            e, ip = ids[:, 0] - 1, ids[:, 1] - 1
            if ip.min() < 0 or ip.max() > 3 or e.min() < 0 or e.max() >= m:
                raise ParseError("stresses: element or integration point out of range")
            seen = np.zeros((m, 4), dtype=np.int64)
            np.add.at(seen, (e, ip), 1)
            if not np.all(seen == 1):
                raise ParseError("stresses: an element integration point is missing or repeated")
            s_ip = np.empty((m, 4, 6))
            s_ip[e, ip] = vals
        elif kind == "total force":
            _, vals = _rows(lines, 0, 3, f"total force {name}")
            if len(vals) != 1:
                raise ParseError(f"total force for {name}: {len(vals)} lines")
            rf[name] = vals[0]
    if u is None:
        raise ParseError("ccx.dat has no displacements for set NALL")
    if s_ip is None:
        raise ParseError("ccx.dat has no stresses for set EALL")
    for name in deck["nsets"]:
        if name.startswith("SUP_") and name not in rf:
            raise ParseError(f"ccx.dat has no total force for set {name}")
    if not (np.all(np.isfinite(u)) and np.all(np.isfinite(s_ip))):
        raise ParseError("ccx.dat holds non-finite displacements or stresses")
    return {**deck, "u": u, "s_ip": s_ip, "rf": rf}


# --------------------------------------------------------------------------------------------------------- metrics

def von_mises(s):
    """von Mises stress of tensors given as (..., 6): sxx, syy, szz, sxy, sxz, syz."""
    import numpy as np

    s = np.asarray(s, dtype=np.float64)
    sxx, syy, szz, sxy, sxz, syz = (s[..., k] for k in range(6))
    return np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3.0 * (sxy ** 2 + sxz ** 2 + syz ** 2))


def weighted_percentile(values, weights, q: float) -> float:
    """The weighted inverted-CDF percentile: sort stably, accumulate the weights, and take the first value whose
    cumulative weight reaches q/100 of the total (times 1 - 1e-12, so q = 100 is the maximum)."""
    import numpy as np

    v = np.asarray(values, dtype=np.float64).reshape(-1)
    w = np.asarray(weights, dtype=np.float64).reshape(-1)
    if v.size == 0:
        return float("nan")
    order = np.argsort(v, kind="stable")
    v, w = v[order], w[order]
    cum = np.cumsum(w)
    i = int(np.searchsorted(cum, float(q) / 100.0 * cum[-1] * (1.0 - 1e-12), side="left"))
    return float(v[min(i, v.size - 1)])


def _excluded(nodes, tets, boxes):
    import numpy as np

    X = np.asarray(nodes, dtype=np.float64)
    cm = X[np.asarray(tets)[:, :4]].mean(axis=1)
    out = np.zeros(len(cm), dtype=bool)
    for b in boxes or []:
        inside = np.ones(len(cm), dtype=bool)
        for a in range(3):
            inside &= (cm[:, a] >= float(b[a]) - _EXCLUDE_TOL) & (cm[:, a] <= float(b[a + 3]) + _EXCLUDE_TOL)
        out |= inside
    return out


def metrics_from(res: dict, material: dict, exclude_boxes) -> tuple[dict, dict]:
    """(metrics, stress) from read_results() output, the material and the voxel solves' stress-exclusion boxes."""
    import numpy as np

    V = tet_volumes(res["nodes"], res["tets"])
    vol = float(np.sum(V))
    u = res["u"]
    cl = np.asarray(res["cload"], dtype=np.float64).reshape(-1, 3)
    comp = float(np.sum(cl[:, 2] * u[cl[:, 0].astype(np.int64), cl[:, 1].astype(np.int64)])) if len(cl) else 0.0
    vm_e = von_mises(res["s_ip"].mean(axis=1))
    vm_ip = von_mises(res["s_ip"])
    excl = _excluded(res["nodes"], res["tets"], exclude_boxes)
    inc = ~excl
    n_inc = int(inc.sum())
    metrics = {"mass_kg": vol * float(material["rho_kg_mm3"]), "mesh_volume_mm3": vol, "compliance_Nmm": comp,
               "umax_mm": float(np.max(np.linalg.norm(u, axis=1))),
               "vm_max_MPa": float(np.max(vm_e[inc])) if n_inc else None,
               "vm_p99_MPa": weighted_percentile(vm_e[inc], V[inc], 99.0) if n_inc else None,
               "vm_p95_MPa": weighted_percentile(vm_e[inc], V[inc], 95.0) if n_inc else None,
               "vm_max_ip_MPa": float(np.max(vm_ip[inc])) if n_inc else None}
    stress = {"measure": "von Mises of each element's mean stress over its 4 integration points; vm_p99 and vm_p95 are "
                         "element-volume-weighted percentiles over the elements outside the exclusion boxes",
              "exclude_boxes": [[float(v) for v in b] for b in exclude_boxes or []], "elements_total": int(len(V)),
              "elements_included": n_inc, "volume_included_mm3": float(np.sum(V[inc]))}
    return metrics, stress


def equilibrium_from(res: dict, support_names: list[str]) -> dict:
    """The applied loads against the support reactions: rel_residual = |sum R + sum F| / |sum F|. ccx's RF total of a
    node set is the nodal force K u summed over the set in all three directions, which at a supported node that also
    carries a load along a free dof includes that load; the reaction of support i is therefore its RF total minus the
    loads applied at its own nodes (rf_totals_N keeps ccx's totals as printed). A support with no set of its own (every
    node already in an earlier support) has a zero total."""
    import numpy as np

    cl = np.asarray(res["cload"], dtype=np.float64).reshape(-1, 3)
    nodes = cl[:, 0].astype(np.int64)
    dofs = cl[:, 1].astype(np.int64)
    applied = np.zeros(3)
    np.add.at(applied, dofs, cl[:, 2])
    per, totals = {}, {}
    reaction = np.zeros(3)
    for i, name in enumerate(support_names):
        key = f"SUP_{i + 1}"
        rf = np.asarray(res["rf"].get(key, np.zeros(3)), dtype=np.float64)
        here = np.zeros(3)
        members = res["nsets"].get(key)
        if members is not None and len(cl):
            m = np.isin(nodes, members)
            np.add.at(here, dofs[m], cl[m, 2])
        r = rf - here
        totals[name] = [float(v) for v in rf]
        per[name] = [float(v) for v in r]
        reaction += r
    mag = float(np.linalg.norm(applied))
    rel = float(np.linalg.norm(reaction + applied)) / mag if mag > 0 else None
    return {"applied_N": [float(v) for v in applied], "reaction_N": [float(v) for v in reaction],
            "per_support_N": per, "rf_totals_N": totals, "rel_residual": rel}


def recompute(case_dir) -> dict:
    """{"metrics", "equilibrium"} re-derived from ccx.inp.gz, ccx.dat.gz and conforming.json's material, supports and
    stress boxes alone: the audit's check that the recorded numbers are what ccx computed."""
    case_dir = Path(case_dir)
    rec = json.loads((case_dir / "conforming.json").read_text(encoding="utf-8"))
    res = read_results(case_dir)
    metrics, _ = metrics_from(res, rec["material"], (rec.get("stress") or {}).get("exclude_boxes") or [])
    names = [s["name"] for s in ((rec.get("bcs") or {}).get("supports") or [])]
    return {"metrics": clean(metrics), "equilibrium": clean(equilibrium_from(res, names))}


# ------------------------------------------------------------------------------------------- bands and comparison

def bands(eff: dict) -> dict:
    """The acceptance bands, fixed before any run: mass and volume 1 %, von Mises max(tol, 15 %), the rest tol."""
    d = eff.get("derived") or {}
    tol = d.get("tol")
    if tol is None:
        tol = (eff.get("verify") or {}).get("tol", 0.10)
    tol = float(tol)
    return {"mass": 0.01, "stress": max(tol, 0.15), "other": tol}


def _band_of(output: str, b: dict) -> float:
    if output in ("mass_kg", "mesh_volume_mm3"):
        return float(b["mass"])
    if output.startswith("vm_"):
        return float(b["stress"])
    return float(b["other"])


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _g(v) -> str:
    return f"{float(v):.4g}" if _finite(v) else "-"


def compare(reference: dict, record: dict, eff: dict) -> tuple[list[dict], bool, list[str]]:
    """Conforming against voxel (section 12.7): one row for the objective, one per constraint, compliance_Nmm as a
    gating row and vm_max_MPa, vm_p95_MPa and mesh_volume_mm3 as reported rows (each only when not already listed).
    Returns (rows, passed, reasons): passed iff the record's status is ok, every objective, constraint and gating row
    agrees within its band and every constraint is met on the conforming mesh."""
    d = eff.get("derived") or {}
    b = bands(eff)
    reference = reference or {}
    record = record or {}
    conf = record.get("metrics") or {}
    obj = (d.get("objective") or {}).get("output")
    cons = list(d.get("constraints") or [])
    rows: list[dict] = []

    def row(o: str, role: str, op=None, limit=None) -> dict:
        v, c = reference.get(o), conf.get(o)
        band = _band_of(o, b)
        r = {"output": o, "role": role, "voxel": v, "conforming": c, "rel_gap": None, "band": band, "agree": False,
             "op": op, "limit": limit, "meets_conforming": None}
        if _finite(v) and _finite(c):
            r["rel_gap"] = (float(c) - float(v)) / max(abs(float(v)), 1e-300)
            r["agree"] = abs(r["rel_gap"]) <= band
        if role == "constraint" and _finite(c) and op in ("<=", ">=") and _finite(limit):
            r["meets_conforming"] = float(c) <= float(limit) if op == "<=" else float(c) >= float(limit)
        elif role == "constraint":
            r["meets_conforming"] = False if record.get("status") == "ok" else None
        return r

    seen = set()
    if obj:
        rows.append(row(obj, "objective"))
        seen.add(obj)
    for c in cons:
        rows.append(row(c["output"], "constraint", c.get("op"), c.get("limit")))
        seen.add(c["output"])
    if "compliance_Nmm" not in seen:
        rows.append(row("compliance_Nmm", "gating"))
        seen.add("compliance_Nmm")
    for o in ("vm_max_MPa", "vm_p95_MPa", "mesh_volume_mm3"):
        if o not in seen:
            rows.append(row(o, "reported"))
            seen.add(o)
    reasons: list[str] = []
    status = record.get("status")
    if status != "ok":
        msg = record.get("message") or "no message"
        reasons.append(f"not run: {status}: {msg}" if status in NOT_RUN else f"{status}: {msg}")
    for r in rows:
        if r["role"] not in ("objective", "constraint", "gating"):
            continue
        if r["rel_gap"] is None:
            if status == "ok":
                reasons.append(f"{r['output']} has no conforming value to compare with the voxel {_g(r['voxel'])}")
        elif not r["agree"]:
            reasons.append(f"{r['output']} {_g(r['conforming'])} vs voxel {_g(r['voxel'])}: {100 * r['rel_gap']:+.1f} % "
                           f"outside the {100 * r['band']:g} % band")
        if r["role"] == "constraint" and r["meets_conforming"] is False and _finite(r["conforming"]):
            word = "exceeds" if r["op"] == "<=" else "is below"
            reasons.append(f"{r['output']} {_g(r['conforming'])} {word} {_g(r['limit'])} on the conforming mesh")
    gating = [r for r in rows if r["role"] in ("objective", "constraint", "gating")]
    passed = (status == "ok" and bool(gating) and all(r["agree"] for r in gating)
              and all(r["meets_conforming"] is True for r in rows if r["role"] == "constraint"))
    return clean(rows), bool(passed), reasons


# ------------------------------------------------------------------------------------------------- the mesher child

def _gmsh_attempt(gmsh, stl_dir: Path, comps: list, case_id: str, route: str, alg: int, size: float, threads: int,
                  netgen: bool) -> None:
    gmsh.clear()
    gmsh.model.add(case_id)
    opt = gmsh.option.setNumber
    opt("General.Terminal", 0)
    opt("General.NumThreads", int(threads))
    opt("Mesh.MeshSizeFromCurvature", 0)
    opt("Mesh.MeshSizeFromPoints", 0)
    opt("Mesh.Algorithm", 6)
    opt("Mesh.Algorithm3D", int(alg))
    opt("Mesh.ElementOrder", 2)
    opt("Mesh.SecondOrderLinear", 1)
    opt("Mesh.Optimize", 1)
    opt("Mesh.OptimizeNetgen", 1 if netgen else 0)
    if route == "remesh":
        gmsh.merge(str(stl_dir / "all.stl"))
        gmsh.model.mesh.classifySurfaces(40.0 * math.pi / 180.0, True, True, 180.0 * math.pi / 180.0)
        gmsh.model.mesh.createGeometry()
        surfs = [s[1] for s in gmsh.model.getEntities(2)]
        loop = gmsh.model.geo.addSurfaceLoop(surfs)
        gmsh.model.geo.addVolume([loop])
        gmsh.model.geo.synchronize()
        opt("Mesh.MeshSizeMin", 0.5 * float(size))
        opt("Mesh.MeshSizeMax", float(size))
        opt("Mesh.MeshSizeExtendFromBoundary", 0)
    else:
        loops = []
        for k in range(len(comps)):
            before = {s[1] for s in gmsh.model.getEntities(2)}
            gmsh.merge(str(stl_dir / f"comp-{k}.stl"))
            new = sorted({s[1] for s in gmsh.model.getEntities(2)} - before)
            loops.append(gmsh.model.geo.addSurfaceLoop(new))
        gmsh.model.geo.addVolume(loops)
        gmsh.model.geo.synchronize()
        opt("Mesh.MeshSizeMax", float(size))
        opt("Mesh.MeshSizeMin", 0.0)
        opt("Mesh.MeshSizeExtendFromBoundary", 1)
    gmsh.model.mesh.generate(3)


def _gmsh_tets(gmsh):
    """(tags, nodes, raw tet10 connectivity as node indices, sicn) of the current model's type-11 elements; nodes are
    the referenced ones in sorted gmsh-tag order."""
    import numpy as np

    types, etags, enodes = gmsh.model.mesh.getElements(3)
    k = [i for i, t in enumerate(types) if int(t) == 11]
    if not k:
        return None
    tags = np.asarray(etags[k[0]], dtype=np.int64)
    conn = np.asarray(enodes[k[0]], dtype=np.int64).reshape(-1, 10)
    ntags, coords, _ = gmsh.model.mesh.getNodes()
    ntags = np.asarray(ntags, dtype=np.int64)
    coords = np.asarray(coords, dtype=np.float64).reshape(-1, 3)
    used = np.unique(conn)
    order = np.argsort(ntags)
    pos = np.searchsorted(ntags[order], used)
    if np.any(pos >= len(ntags)) or np.any(ntags[order][np.minimum(pos, len(ntags) - 1)] != used):
        raise RuntimeError("an element references a node gmsh did not return")
    X = coords[order][pos]
    raw = np.searchsorted(used, conn)
    sicn = np.asarray(gmsh.model.mesh.getElementQualities(tags.tolist(), "minSICN"), dtype=np.float64)
    return tags, X, raw, sicn


def mesh_stl(stl, out_dir, *, size: float, threads: int, case_id: str = "part", attempts: str = "ABCD") -> dict:
    """The mesher (section 12.2): the STL checked and cleaned, then up to four gmsh attempts (A remesh/Delaunay, only
    without cavities; B remesh/HXT; C discrete/Delaunay; D discrete/HXT), each gated on type-11 elements, a positive
    minimum SICN (after one Netgen optimisation if needed) and, for the remesh route, the volume within
    REMESH_VOLUME_TOL. Writes mesh.npz and tetmesh.json in out_dir and returns the tetmesh record (status ok, or
    not-watertight, multi-body, mesh-failed, invalid-mesh, error). Runs in the child process; prints to mesh.log."""
    import numpy as np

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    size = float(size)
    rec = {"status": "error", "message": "", "route": None, "algorithm_3d": None, "size": size, "nodes": None,
           "elements": None, "dof": None, "min_sicn": None, "p01_sicn": None, "volume_mm3": None,
           "volume_rel_err": None, "attempts": [], "source": None, "gmsh_version": None}
    t0 = time.time()
    try:
        m, comps, info = _load_stl(stl)
        rec["source"] = info
        if not info["watertight"]:
            rec.update(status="not-watertight", message=f"{Path(stl).name} is not watertight: no conforming mesh")
            return rec
        shells = sum(1 for _, v in comps if v > 0)
        if shells != 1:
            rec.update(status="multi-body" if shells > 1 else "not-watertight",
                       message=(f"{shells} closed shells with positive volume: one body only" if shells > 1 else
                                "no closed shell with positive volume"))
            return rec
        v_stl = float(info["volume_mm3"])
        import gmsh

        rec["gmsh_version"] = getattr(gmsh, "__version__", None)
        tmp = Path(tempfile.mkdtemp(prefix=".mesh-", dir=str(out_dir)))
        try:
            m.export(str(tmp / "all.stl"))
            for k, (c, _) in enumerate(comps):
                c.export(str(tmp / f"comp-{k}.stl"))
            gmsh.initialize(["gmsh", "-noenv"], readConfigFiles=False)
            gmsh.logger.start()
            try:
                best = _attempts(gmsh, tmp, comps, case_id, size, int(threads), v_stl, rec, attempts)
            finally:
                for line in gmsh.logger.get():
                    print(f"gmsh: {line}")
                gmsh.logger.stop()
                gmsh.finalize()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if best is None:
            return rec
        X, raw, sicn, route, alg = best
        tets = order_tet10(X, raw)
        np.savez(out_dir / "mesh.npz", nodes=X, tets=tets)
        vol = float(np.sum(tet_volumes(X, tets)))
        rec.update(status="ok", message="", route=route, algorithm_3d=alg, nodes=int(len(X)), elements=int(len(tets)),
                   dof=3 * int(len(X)), min_sicn=float(np.min(sicn)),
                   p01_sicn=float(np.percentile(sicn, _SICN_P)), volume_mm3=vol,
                   volume_rel_err=(vol - v_stl) / v_stl)
        return rec
    except Exception as exc:  # recorded, never raised: the parent reads tetmesh.json
        traceback.print_exc()
        rec.update(status="error", message=f"{type(exc).__name__}: {exc}"[:400])
        return rec
    finally:
        rec["wall_s"] = time.time() - t0
        dump_json(out_dir / "tetmesh.json", rec)


def _attempts(gmsh, tmp: Path, comps: list, case_id: str, size: float, threads: int, v_stl: float, rec: dict,
              allowed: str = "ABCD"):
    import numpy as np

    cavities = sum(1 for _, v in comps if v <= 0)
    last_quality = False
    for name, route, alg in ATTEMPTS:
        if name not in allowed or (name == "A" and cavities):
            continue
        t = time.time()
        att = {"attempt": name, "route": route, "algorithm_3d": alg, "ok": False, "error": None, "wall_s": None,
               "elements": None, "volume_rel_err": None, "min_sicn": None, "netgen": True}
        rec["attempts"].append(att)
        try:
            try:
                _gmsh_attempt(gmsh, tmp, comps, case_id, route, alg, size, threads, netgen=True)
            except Exception as exc:
                print(f"attempt {name}: generation with Netgen raised {type(exc).__name__}: {exc}; retried without it")
                att["netgen"] = False
                _gmsh_attempt(gmsh, tmp, comps, case_id, route, alg, size, threads, netgen=False)
            got = _gmsh_tets(gmsh)
            if got is None:
                raise RuntimeError("gmsh produced no 10-node tetrahedra")
            _, X, raw, sicn = got
            att["elements"] = int(len(raw))
            att["min_sicn"] = float(np.min(sicn))
            if not att["min_sicn"] > 0:
                gmsh.model.mesh.optimize("Netgen")
                got = _gmsh_tets(gmsh)
                if got is None:
                    raise RuntimeError("gmsh produced no 10-node tetrahedra after the Netgen optimisation")
                _, X, raw, sicn = got
                att["elements"] = int(len(raw))
                att["min_sicn"] = float(np.min(sicn))
            vol = float(np.sum(tet_volumes(X, raw))) if len(raw) else 0.0
            att["volume_rel_err"] = (vol - v_stl) / v_stl
            if not att["min_sicn"] > 0:
                last_quality = True
                raise RuntimeError(f"minimum SICN {att['min_sicn']:.3g} is not positive (an inverted or flat element)")
            if route == "remesh" and abs(att["volume_rel_err"]) > REMESH_VOLUME_TOL:
                last_quality = False
                raise RuntimeError(f"the remeshed volume differs from the STL's by {100 * att['volume_rel_err']:+.3f} % "
                                   f"(limit {100 * REMESH_VOLUME_TOL:g} %)")
            att["ok"] = True
            return X, raw, sicn, route, alg
        except Exception as exc:
            text = str(exc).strip().splitlines()
            att["error"] = (f"{type(exc).__name__}: {text[0] if text else ''}")[:200]
            if "SICN" not in att["error"]:
                last_quality = False
            print(f"attempt {name} ({route}, Algorithm3D {alg}) failed: {att['error']}")
        finally:
            att["wall_s"] = time.time() - t
    rec["status"] = "invalid-mesh" if last_quality else "mesh-failed"
    tried = "; ".join(f"{a['attempt']}: {a['error']}" for a in rec["attempts"])
    rec["message"] = f"every mesh attempt failed ({tried})"[:600]
    return None


# --------------------------------------------------------------------------------------------------------- the check

def _empty_record(case_id: str, where: str, threads: int, timeout_s: float, max_dof: int) -> dict:
    return {"schema": SCHEMA, "case_id": case_id, "where": where, "status": None, "message": None,
            "started_utc": utc_now(), "finished_utc": None, "wall_s": None, "wall_s_mesh": None, "wall_s_solve": None,
            "wall_s_post": None, "host": socket.gethostname(), "threads": int(threads), "timeout_s": float(timeout_s),
            "max_dof": int(max_dof),
            "source": {"stl": None, "sha256": None, "faces": None, "vertices": None, "volume_mm3": None,
                       "area_mm2": None, "watertight": None, "shells": None, "cavities": None, "bbox_mm": None},
            "mesh": {"tool": "gmsh", "version": None, "route": None, "algorithm_3d": None, "size_mm": None,
                     "element": "C3D10", "second_order_linear": True, "nodes": None, "elements": None, "dof": None,
                     "dof_estimate": None, "volume_mm3": None, "volume_rel_err": None, "min_sicn": None,
                     "p01_sicn": None, "attempts": None},
            "bcs": {"window_mm": None, "normal_min": NORMAL_MIN, "supports": [], "loads": []},
            "solver": {"tool": "CalculiX ccx", "version": None, "path": None, "sha256": None, "linear_solver": None,
                       "card": None, "threads": int(threads), "rc": None, "wall_s": None, "errors": [], "warnings": []},
            "equilibrium": {"applied_N": None, "reaction_N": None, "per_support_N": None, "rf_totals_N": None,
                            "rel_residual": None},
            "metrics": {"mass_kg": None, "mesh_volume_mm3": None, "compliance_Nmm": None, "umax_mm": None,
                        "vm_max_MPa": None, "vm_p99_MPa": None, "vm_p95_MPa": None, "vm_max_ip_MPa": None},
            "stress": {"measure": None, "exclude_boxes": None, "elements_total": None, "elements_included": None,
                       "volume_included_mm3": None},
            "material": {"E_MPa": None, "nu": None, "rho_kg_mm3": None}, "files": {}, "artifacts": []}


def _gzip(src: Path) -> Path:
    dst = src.with_name(src.name + ".gz")
    with open(src, "rb") as fin, open(dst, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=6, fileobj=raw, mtime=0) as gz:
            shutil.copyfileobj(fin, gz, 1 << 20)
    src.unlink()
    return dst


def _child_env() -> dict:
    env = dict(os.environ)
    lib = str(Path(__file__).resolve().parents[2])           # the directory that holds aces_engineering/
    env["PYTHONPATH"] = lib + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _run_mesher(stl: Path, out_dir: Path, size: float, threads: int, case_id: str) -> tuple[str, str, dict | None]:
    """(status, message, tetmesh record) of the child mesher; stdout and stderr go to mesh.log."""
    cmd = [sys.executable, "-m", "aces_engineering.golden.conforming", "mesh", "--stl", str(stl), "--out", str(out_dir),
           "--size", repr(float(size)), "--threads", str(int(threads)), "--case-id", case_id]
    tm = out_dir / "tetmesh.json"
    with contextlib.suppress(FileNotFoundError):
        tm.unlink()
    with open(out_dir / "mesh.log", "w", encoding="utf-8", newline="\n") as log:
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        try:
            p = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=_child_env(),
                               cwd=str(out_dir), timeout=MESH_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return "mesh-timeout", f"gmsh did not finish within {MESH_TIMEOUT_S:g} s (the child was killed)", None
    try:
        tetmesh = json.loads(tm.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tail = (out_dir / "mesh.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:] or [""]
        return "mesh-failed", f"the mesher exited {p.returncode} without tetmesh.json ({tail[0][:200]})", None
    status = tetmesh.get("status")
    if status not in CHILD_STATUSES:
        return "error", f"the mesher reported an unknown status {status!r}", tetmesh
    return status, tetmesh.get("message") or "", tetmesh


def _solver_line(log_text: str) -> str | None:
    for line in log_text.splitlines():
        low = line.lower()
        if "solver" in low and ("spooles" in low or "iterative" in low or "cholesky" in low or "pardiso" in low
                                or "pastix" in low or "equations" in low):
            return line.strip()
    return None


def _ccx_messages(text: str, word: str) -> list[str]:
    return [ln.strip()[:300] for ln in text.splitlines() if word in ln][:20]


def run_check(stl_path, eff: dict, domain: dict, out_dir, *, case_id: str, size_mm: float, where: str, threads: int,
              timeout_s: float | None = None, max_dof: int | None = None, ccx: str | None = None) -> dict:
    """The conforming check of one part (section 12): mesh in a child process, map the supports and loads, write the
    deck, run ccx, parse, measure. Always writes out_dir/conforming.json and returns it; never raises for a modelling
    failure (see the module docstring for the statuses)."""
    import numpy as np

    if where not in MAX_DOF:
        raise ValueError(f"where must be one of {sorted(MAX_DOF)}, not {where!r}")
    # absolute paths: the mesher child runs with cwd = out_dir, where a relative --stl or --out names another file
    # (the rig worker hands its adapter a run directory relative to /work)
    out_dir = Path(os.path.abspath(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    timeout_s = float(TIMEOUT_S[where] if timeout_s is None else timeout_s)
    max_dof = int(MAX_DOF[where] if max_dof is None else max_dof)
    rec = _empty_record(case_id, where, threads, timeout_s, max_dof)
    rec["mesh"]["size_mm"] = float(size_mm)
    t0 = time.time()
    stl = Path(os.path.abspath(stl_path))
    for name in CASE_FILES + ("conforming.json", "ccx.inp", "ccx.dat", "ccx.frd", "ccx.12d", "spooles.out"):
        with contextlib.suppress(FileNotFoundError):     # a rerun in the same case directory starts clean
            (out_dir / name).unlink()

    def finish(status: str, message: str = "") -> dict:
        rec["status"], rec["message"] = status, message
        rec["finished_utc"] = utc_now()
        rec["wall_s"] = time.time() - t0
        files = {}
        for name in CASE_FILES:
            p = out_dir / name
            if p.is_file():
                files[name] = sha256_file(p)
        rec["files"] = files
        rec["artifacts"] = sorted(files)
        dump_json(out_dir / "conforming.json", rec)
        return rec

    try:
        bc = bc_from(eff, domain)
        rec["material"] = dict(bc["material"])
        rec["bcs"]["window_mm"] = bc["window_mm"]
        rec["stress"]["exclude_boxes"] = bc["exclude_boxes"]
        try:
            rel = os.path.relpath(stl.resolve(), out_dir.resolve().parent)
            rec["source"]["stl"] = Path(rel).as_posix() if not rel.startswith("..") else str(stl.resolve())
        except ValueError:
            rec["source"]["stl"] = str(stl.resolve())
        if stl.is_file():
            rec["source"]["sha256"] = sha256_file(stl)
        av = available(ccx)
        rec["mesh"]["version"] = av["gmsh"]
        rec["solver"].update(version=av["ccx_version"], path=av["ccx"], sha256=av["ccx_sha256"])
        if not av["gmsh"]:
            return finish("gmsh-not-found", av["gmsh_reason"] or "gmsh not found")
        if not av["usable"]:
            return finish("ccx-not-found", av["reason"] or "ccx not found")
        if not stl.is_file():
            return finish("error", f"{stl} does not exist")
        _, _, info = _load_stl(stl)
        rec["source"].update({k: info.get(k) for k in ("faces", "vertices", "volume_mm3", "area_mm2", "watertight",
                                                        "shells", "cavities", "bbox_mm")})
        if not info["watertight"]:
            return finish("not-watertight", f"{stl.name} is not watertight: no conforming mesh")
        if (info.get("shells") or 0) > 1:
            return finish("multi-body", f"{info['shells']} closed shells with positive volume: one body only")
        est = estimate_dof(float(info["volume_mm3"]), float(size_mm))
        rec["mesh"]["dof_estimate"] = est
        if est > max_dof:
            return finish("too-large", f"about {est} DOF at {float(size_mm):g} mm (37 V / h^3) exceeds max_dof "
                                       f"{max_dof} on the {where}; the size is never coarsened silently")

        # 1. the mesh, in a child process
        tm0 = time.time()
        status, message, tetmesh = _run_mesher(stl, out_dir, float(size_mm), int(threads), case_id)
        rec["wall_s_mesh"] = time.time() - tm0
        if tetmesh:
            rec["mesh"].update(route=tetmesh.get("route"), algorithm_3d=tetmesh.get("algorithm_3d"),
                               nodes=tetmesh.get("nodes"), elements=tetmesh.get("elements"), dof=tetmesh.get("dof"),
                               volume_mm3=tetmesh.get("volume_mm3"), volume_rel_err=tetmesh.get("volume_rel_err"),
                               min_sicn=tetmesh.get("min_sicn"), p01_sicn=tetmesh.get("p01_sicn"),
                               attempts=tetmesh.get("attempts"))
            rec["mesh"]["version"] = tetmesh.get("gmsh_version") or rec["mesh"]["version"]
        if status != "ok":
            return finish(status, message)
        if 3 * int(tetmesh["nodes"]) > max_dof:
            return finish("too-large", f"the mesh has {3 * int(tetmesh['nodes'])} DOF at {float(size_mm):g} mm, over "
                                       f"max_dof {max_dof} on the {where}")
        with np.load(out_dir / "mesh.npz") as z:
            X, T = z["nodes"], z["tets"]

        # 2. supports and loads on the surface
        try:
            mapped = map_bcs(X, T, bc)
        except MappingError as exc:
            rec["bcs"]["supports"] = [{k: v for k, v in s.items() if k != "node_ids"} for s in exc.partial["supports"]]
            rec["bcs"]["loads"] = list(exc.partial["loads"])
            return finish("bc-mapping-failed", str(exc))
        rec["bcs"]["supports"] = [{k: v for k, v in s.items() if k != "node_ids"} for s in mapped["supports"]]
        rec["bcs"]["loads"] = list(mapped["loads"])

        # 3. the deck and the solve
        heading = f"aces golden conforming check {case_id}: gmsh C3D10 (second-order linear) of {stl.name}"
        deck = write_deck(out_dir / "ccx.inp", X, T, mapped, bc["material"], heading=heading)
        env = dict(os.environ)
        for k in ("OMP_NUM_THREADS", "CCX_NPROC_EQUATION_SOLVER", "CCX_NPROC_STIFFNESS", "CCX_NPROC_RESULTS"):
            env[k] = str(int(threads))
        ts0 = time.time()
        rc, timed_out = None, False
        with open(out_dir / "ccx.log", "w", encoding="utf-8", newline="\n") as log:
            try:
                p = subprocess.run([av["ccx"], "ccx"], cwd=str(out_dir), env=env, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, timeout=timeout_s)
                rc = p.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
        rec["wall_s_solve"] = time.time() - ts0
        rec["solver"].update(rc=rc, wall_s=rec["wall_s_solve"])
        log_text = (out_dir / "ccx.log").read_text(encoding="utf-8", errors="replace")
        rec["solver"]["errors"] = _ccx_messages(log_text, "*ERROR")
        rec["solver"]["warnings"] = _ccx_messages(log_text, "*WARNING")
        rec["solver"]["linear_solver"] = _solver_line(log_text)   # ccx's own words; None when it named none
        rec["solver"]["card"] = f"*STATIC, SOLVER={deck['linear_solver_card']}"
        for junk in ("ccx.frd", "ccx.12d", "spooles.out"):
            with contextlib.suppress(FileNotFoundError):
                (out_dir / junk).unlink()
        tp0 = time.time()
        _gzip(out_dir / "ccx.inp")
        if timed_out:
            rec["wall_s_post"] = time.time() - tp0
            return finish("ccx-timeout", f"ccx did not finish within {timeout_s:g} s (killed)")
        dat = out_dir / "ccx.dat"
        if rc != 0 or rec["solver"]["errors"] or not dat.is_file():
            first = rec["solver"]["errors"][0] if rec["solver"]["errors"] else None
            why = first or (f"ccx exited {rc}" if rc != 0 else "ccx wrote no ccx.dat")
            if dat.is_file():
                _gzip(dat)
            rec["wall_s_post"] = time.time() - tp0
            return finish("ccx-failed", why)
        _gzip(dat)

        # 4. parse and measure (from the gzipped files, exactly as recompute() does)
        try:
            res = read_results(out_dir)
        except ParseError as exc:
            rec["wall_s_post"] = time.time() - tp0
            return finish("parse-failed", str(exc))
        metrics, stress = metrics_from(res, bc["material"], bc["exclude_boxes"])
        rec["metrics"] = metrics
        rec["stress"] = stress
        rec["equilibrium"] = equilibrium_from(res, [s["name"] for s in mapped["supports"]])
        rec["wall_s_post"] = time.time() - tp0
        if any(metrics[k] is None for k in metrics):
            return finish("error", "every element lies in a stress-exclusion zone: no von Mises measure")
        rel = rec["equilibrium"]["rel_residual"]
        if rel is None or not rel <= EQUILIBRIUM_TOL:
            return finish("equilibrium", f"the reactions do not balance the loads: relative residual "
                                         f"{_g(rel)} > {EQUILIBRIUM_TOL:g}")
        return finish("ok", f"{rec['mesh']['elements']} C3D10, {rec['mesh']['dof']} DOF, {rec['mesh']['route']} route; "
                            f"equilibrium {rel:.2e}")
    except Exception as exc:  # recorded, never raised (section 12.1)
        traceback.print_exc()
        return finish("error", f"{type(exc).__name__}: {exc}"[:400])


# ------------------------------------------------------------------------------------------------ the patch test

# Section 12.8: a 10 x 2 x 2 mm bar under a uniform 25 MPa tension, held by three symmetry planes. A tet10 mesh
# reproduces the linear exact field, so every value below is closed form: tip u_x = F L / (E A), the corner's |u| with
# the lateral contraction nu F / (E A) * 2 mm on y and z, compliance F u_x, mass rho * 40 mm3.
PATCH_BAR = {"E_MPa": 200000.0, "nu": 0.3, "rho_kg_mm3": 7.85e-6, "size_mm": 1.0, "force_N": 100.0}
PATCH_EXPECT = {"tip_ux_mm": 1.25e-3, "umax_mm": 1.2544919e-3, "vm_p99_MPa": 25.0, "vm_max_MPa": 25.0,
                "compliance_Nmm": 0.125, "mass_kg": 3.14e-4}
PATCH_TOL = 1e-6


def write_box_stl(path, lo, hi) -> Path:
    """A binary STL of an axis-aligned box (12 triangles, outward normals), written with struct."""
    import struct

    x0, y0, z0 = (float(v) for v in lo)
    x1, y1, z1 = (float(v) for v in hi)
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = (((0, 3, 2, 1), (0, 0, -1)), ((4, 5, 6, 7), (0, 0, 1)), ((0, 1, 5, 4), (0, -1, 0)),
             ((2, 3, 7, 6), (0, 1, 0)), ((1, 2, 6, 5), (1, 0, 0)), ((0, 4, 7, 3), (-1, 0, 0)))
    body = b""
    for (a, b, c, d), n in quads:
        for tri in ((a, b, c), (a, c, d)):
            body += struct.pack("<3f", *n) + b"".join(struct.pack("<3f", *v[k]) for k in tri) + b"\0\0"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"aces golden box".ljust(80, b" ") + struct.pack("<I", 12) + body)
    return path


def bar_patch_test(work, *, threads: int = 1, ccx: str | None = None, where: str = "desktop") -> dict:
    """Run the section 12.8 bar patch test through run_check() in work/ (bar.stl, m001/). Returns {"status", "ok",
    "record", "checks": {name: {"got", "want", "rel"}}, "equilibrium"}; "ok" is None when the check did not run (a
    status in NOT_RUN, e.g. ccx-not-found), else True iff every value is within PATCH_TOL and equilibrium holds."""
    import numpy as np

    work = Path(work)
    stl = write_box_stl(work / "bar.stl", (0, 0, 0), (10, 2, 2))
    f = PATCH_BAR["force_N"]
    eff = {"material": {"name": "patch-test steel (closed-form test input)", "E_MPa": PATCH_BAR["E_MPa"],
                        "nu": PATCH_BAR["nu"], "rho_kg_mm3": PATCH_BAR["rho_kg_mm3"], "source": "test input"},
           "supports": [{"name": "x0", "box": [0, 0, 0, 0, 2, 2], "dofs": [0]},
                        {"name": "y0", "box": [0, 0, 0, 10, 0, 2], "dofs": [1]},
                        {"name": "z0", "box": [0, 0, 0, 10, 2, 0], "dofs": [2]}],
           "loads": [{"name": "tip", "box": [10, 0, 0, 10, 2, 2], "force": [f, 0.0, 0.0], "axis": "x",
                      "case": "default"}],
           "derived": {"fea_mm": PATCH_BAR["size_mm"], "tol": 0.10,
                       "grid": {"origin_mm": [0.0, 0.0, 0.0], "envelope_mm": [10.0, 2.0, 2.0]},
                       "objective": {"output": "mass_kg", "sense": "min"}, "constraints": []}}
    rec = run_check(stl, eff, {"stress_exclude": []}, work / "m001", case_id="m001", size_mm=PATCH_BAR["size_mm"],
                    where=where, threads=threads, ccx=ccx)
    out = {"status": rec["status"], "ok": None, "record": rec, "checks": {}, "equilibrium": None}
    if rec["status"] in NOT_RUN:
        return out
    if rec["status"] != "ok":
        out["ok"] = False
        return out
    res = read_results(work / "m001")
    tip = np.abs(res["nodes"][:, 0] - 10.0) <= 1e-9
    got = {"tip_ux_mm": float(np.mean(res["u"][tip, 0])), **{k: rec["metrics"][k] for k in PATCH_EXPECT if k != "tip_ux_mm"}}
    for k, want in PATCH_EXPECT.items():
        g = got[k]
        out["checks"][k] = {"got": g, "want": want, "rel": abs(g - want) / abs(want)}
    out["equilibrium"] = rec["equilibrium"]["rel_residual"]
    out["ok"] = (all(c["rel"] <= PATCH_TOL for c in out["checks"].values())
                 and out["equilibrium"] is not None and out["equilibrium"] <= EQUILIBRIUM_TOL)
    return out


# ------------------------------------------------------------------------------------------------------------- main

def main(argv=None) -> int:
    """`python -m aces_engineering.golden.conforming mesh --stl S --out D --size H --threads N [--case-id ID]`: the
    mesher child of run_check(). Writes D/mesh.npz and D/tetmesh.json; exit 0 when the mesh passed its gates, 1 when
    tetmesh.json records a failure, 2 on a usage error."""
    ap = argparse.ArgumentParser(prog="python -m aces_engineering.golden.conforming",
                                 description="the conforming check's mesher (run by run_check in a child process)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mp = sub.add_parser("mesh", help="C3D10 mesh of a watertight STL")
    mp.add_argument("--stl", required=True)
    mp.add_argument("--out", required=True)
    mp.add_argument("--size", required=True, type=float)
    mp.add_argument("--threads", type=int, default=1)
    mp.add_argument("--case-id", default="part")
    mp.add_argument("--attempts", default="ABCD", help="which of the attempts A-D to try, in order (default: all; "
                                                         "tests pin a route with it)")
    a = ap.parse_args(argv)
    if not a.size > 0 or a.threads < 1 or not a.attempts or set(a.attempts) - set("ABCD"):
        print("--size must be positive, --threads at least 1 and --attempts letters from ABCD", file=sys.stderr)
        return 2
    rec = mesh_stl(a.stl, a.out, size=a.size, threads=a.threads, case_id=a.case_id, attempts=a.attempts)
    print(f"mesh: {rec['status']}: " + (rec.get("message") or f"{rec.get('elements')} C3D10, {rec.get('nodes')} nodes, "
                                                                f"route {rec.get('route')}"))
    return 0 if rec["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
