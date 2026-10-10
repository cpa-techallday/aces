"""B3 unit tests: golden.conforming, the conforming second discretisation (build spec section 12.8).

Pure numpy (always run; every mesh, record and number here is a labelled test fixture or a closed form): C3D10 midside
placement and the orientation fix, boundary faces of a cube in six tets, face selection (footprint, window, facing),
consistent traction loads, the weighted percentile, read_results on an embedded ccx deck and .dat, equilibrium,
recompute, compare, bands and the status paths of run_check that need neither gmsh nor ccx. The embedded deck and .dat
are REAL output of the official CalculiX ccx 2.23 binary (ccx_2.23.tar.bz2 sha256 35d426fe...55dd), run on DARKNESS on
2026-10-10 in a throwaway Ubuntu 24.04 container (B3's test image, with the GCC 7 Fortran runtime the binary needs),
on a deck written by conforming.write_deck: a 2 mm cube in six C3D10 under a 40 N uniaxial patch load. Their sha256 are
checked below. gmsh tests (skipped when gmsh is not importable): a 10 mm cube on the remesh route, an icosphere and a
sphere inside a sphere on the discrete route, through the real mesher child process. ccx tests (skipped without a
usable ccx): the bar patch test and the cantilever of section 12.8 through run_check. A mesh above SPOOLES_MAX_DOF is
too-large and never solved (review finding 1): with gmsh and a stand-in ccx that must not run, and with the real ccx.
Run: python -m unittest discover -s tests -p "test_g4_conforming.py"."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import itertools
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from aces_engineering.golden import conforming as cf

HAVE_GMSH = importlib.util.find_spec("gmsh") is not None
try:
    import trimesh  # noqa: F401  (core lock; the icosphere fixtures)
    HAVE_TRIMESH = True
except ImportError:
    HAVE_TRIMESH = False
_AV = cf.available()
HAVE_CCX = bool(_AV["usable"])
CCX_WHY = _AV["reason"] or "ccx not usable"

FIXTURE_INP_SHA256 = "9e791681109df331b247b4267ee9cfed064ee5a312adbd1225dbd2e6bccbe531"
FIXTURE_DAT_SHA256 = "6dda7cee4614d1e18dde78cfdbde6cd1825a535d3a1a4b85a62032b7bb809107"
# real ccx 2.23 input and output (see the module docstring); test fixture, not a measurement of a part
FIXTURE_INP = """*HEADING
aces golden test fixture: 2 mm cube, six C3D10, uniaxial patch test (40 N on x=2)
*NODE, NSET=NALL
1, 0.0, 0.0, 0.0
2, 2.0, 0.0, 0.0
3, 0.0, 2.0, 0.0
4, 2.0, 2.0, 0.0
5, 0.0, 0.0, 2.0
6, 2.0, 0.0, 2.0
7, 0.0, 2.0, 2.0
8, 2.0, 2.0, 2.0
9, 1.0, 0.0, 0.0
10, 2.0, 1.0, 0.0
11, 1.0, 1.0, 0.0
12, 1.0, 1.0, 1.0
13, 2.0, 1.0, 1.0
14, 2.0, 2.0, 1.0
15, 2.0, 0.0, 1.0
16, 1.0, 0.0, 1.0
17, 2.0, 1.0, 2.0
18, 0.0, 1.0, 0.0
19, 1.0, 2.0, 0.0
20, 1.0, 2.0, 1.0
21, 0.0, 2.0, 1.0
22, 0.0, 1.0, 1.0
23, 1.0, 2.0, 2.0
24, 0.0, 0.0, 1.0
25, 1.0, 0.0, 2.0
26, 1.0, 1.0, 2.0
27, 0.0, 1.0, 2.0
*ELEMENT, TYPE=C3D10, ELSET=EALL
1, 1, 2, 4, 8, 9, 10, 11, 12, 13, 14
2, 1, 6, 2, 8, 16, 15, 9, 12, 17, 13
3, 1, 4, 3, 8, 11, 19, 18, 12, 14, 20
4, 1, 3, 7, 8, 18, 21, 22, 12, 20, 23
5, 1, 5, 6, 8, 24, 25, 16, 12, 26, 17
6, 1, 7, 5, 8, 22, 27, 24, 12, 23, 26
*NSET, NSET=SUP_1
1, 3, 5, 7, 18, 21, 22, 24, 27
*NSET, NSET=SUP_2
2, 6, 9, 15, 16, 25
*NSET, NSET=SUP_3
4, 10, 11, 19
*MATERIAL, NAME=MAT
*ELASTIC
200000.0, 0.3
*SOLID SECTION, ELSET=EALL, MATERIAL=MAT
*STEP
*STATIC, SOLVER=SPOOLES
*BOUNDARY
SUP_1, 1, 1
SUP_2, 2, 2
SUP_3, 3, 3
1, 2, 2
5, 2, 2
24, 2, 2
1, 3, 3
3, 3, 3
18, 3, 3
2, 3, 3
9, 3, 3
*CLOAD
10, 1, 6.666666666666666
13, 1, 13.333333333333332
14, 1, 6.666666666666666
15, 1, 6.666666666666666
17, 1, 6.666666666666666
*NODE PRINT, NSET=NALL
U
*NODE PRINT, NSET=SUP_1, TOTALS=ONLY
RF
*NODE PRINT, NSET=SUP_2, TOTALS=ONLY
RF
*NODE PRINT, NSET=SUP_3, TOTALS=ONLY
RF
*EL PRINT, ELSET=EALL
S
*END STEP
"""
FIXTURE_DAT = """
                        S T E P       1


                                INCREMENT     1


 displacements (vx,vy,vz) for set NALL and time  0.1000000E+01

         1  0.000000E+00  0.000000E+00  0.000000E+00
         2  1.000000E-04  0.000000E+00  0.000000E+00
         3  0.000000E+00 -3.000000E-05  0.000000E+00
         4  1.000000E-04 -3.000000E-05  0.000000E+00
         5  0.000000E+00  0.000000E+00 -3.000000E-05
         6  1.000000E-04  0.000000E+00 -3.000000E-05
         7  0.000000E+00 -3.000000E-05 -3.000000E-05
         8  1.000000E-04 -3.000000E-05 -3.000000E-05
         9  5.000000E-05  0.000000E+00  0.000000E+00
        10  1.000000E-04 -1.500000E-05  0.000000E+00
        11  5.000000E-05 -1.500000E-05  0.000000E+00
        12  5.000000E-05 -1.500000E-05 -1.500000E-05
        13  1.000000E-04 -1.500000E-05 -1.500000E-05
        14  1.000000E-04 -3.000000E-05 -1.500000E-05
        15  1.000000E-04  0.000000E+00 -1.500000E-05
        16  5.000000E-05  0.000000E+00 -1.500000E-05
        17  1.000000E-04 -1.500000E-05 -3.000000E-05
        18  0.000000E+00 -1.500000E-05  0.000000E+00
        19  5.000000E-05 -3.000000E-05  0.000000E+00
        20  5.000000E-05 -3.000000E-05 -1.500000E-05
        21  0.000000E+00 -3.000000E-05 -1.500000E-05
        22  0.000000E+00 -1.500000E-05 -1.500000E-05
        23  5.000000E-05 -3.000000E-05 -3.000000E-05
        24  0.000000E+00  0.000000E+00 -1.500000E-05
        25  5.000000E-05  0.000000E+00 -3.000000E-05
        26  5.000000E-05 -1.500000E-05 -3.000000E-05
        27  0.000000E+00 -1.500000E-05 -3.000000E-05

 total force (fx,fy,fz) for set SUP_1 and time  0.1000000E+01

       -4.000000E+01  5.842754E-15  7.548686E-15

 total force (fx,fy,fz) for set SUP_2 and time  0.1000000E+01

        6.666667E+00 -1.485933E-14 -4.629031E-15

 total force (fx,fy,fz) for set SUP_3 and time  0.1000000E+01

        6.666667E+00  2.876298E-15  3.771493E-15

 stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz) for set EALL and time  0.1000000E+01

         1   1  1.000000E+01  8.881784E-16  1.776357E-15 -2.475942E-15  1.433440E-15 -4.560947E-16
         1   2  1.000000E+01  8.881784E-15  1.509903E-14  1.433440E-15  4.560947E-15 -7.167202E-16
         1   3  1.000000E+01  2.664535E-15  2.664535E-15  5.864074E-16 -1.694066E-15 -2.019848E-15
         1   4  1.000000E+01  4.440892E-15  1.154632E-14 -5.212510E-15 -3.909383E-15 -2.345630E-15
         2   1  1.000000E+01  3.552714E-15 -4.440892E-15  2.671412E-15 -3.518445E-15  2.215317E-15
         2   2  1.000000E+01  1.598721E-14  7.993606E-15 -1.954691E-16 -1.433440E-15  1.042502E-15
         2   3  1.000000E+01  1.154632E-14  1.154632E-14  3.713914E-15  6.515638E-16  1.172815E-15
         2   4  1.000000E+01  7.993606E-15  7.105427E-15 -5.212510E-16 -8.340017E-15 -8.470329E-16
         3   1  1.000000E+01  8.881784E-16 -7.993606E-15 -2.997194E-15  5.864074E-16  1.303128E-16
         3   2  1.000000E+01  3.552714E-15  8.881784E-16 -2.997194E-15 -2.671412E-15 -1.172815E-15
         3   3  1.000000E+01 -1.776357E-15 -2.398082E-14 -2.997194E-15  1.759222E-15  5.212510E-16
         3   4  1.000000E+01 -2.664535E-15 -1.776357E-15 -2.606255E-15 -6.385325E-15  1.042502E-15
         4   1  1.000000E+01  3.552714E-15 -3.552714E-15 -1.563753E-15 -2.085004E-15  2.606255E-16
         4   2  1.000000E+01  5.329071E-15 -1.332268E-14 -1.954691E-15 -2.345630E-15  1.042502E-15
         4   3  1.000000E+01 -1.776357E-15 -2.664535E-15 -6.515638E-16 -2.085004E-15  7.818766E-16
         4   4  1.000000E+01 -2.664535E-15 -1.776357E-15 -2.085004E-15 -6.515638E-15  1.563753E-15
         5   1  1.000000E+01 -3.552714E-15 -5.329071E-15  1.303128E-15 -2.475942E-15  2.280473E-15
         5   2  1.000000E+01 -1.065814E-14  8.881784E-16 -1.694066E-15 -1.954691E-15  3.192663E-15
         5   3  1.000000E+01  9.769963E-15  4.440892E-15 -2.215317E-15 -3.909383E-16  3.257819E-16
         5   4  1.000000E+01 -1.776357E-15 -8.881784E-16  2.606255E-16 -4.691259E-15 -1.824379E-15
         6   1  1.000000E+01  0.000000E+00 -3.552714E-15 -1.433440E-15 -2.085004E-15  1.368284E-15
         6   2  1.000000E+01 -6.217249E-15 -3.552714E-15 -2.606255E-16 -2.866881E-15  2.150161E-15
         6   3  1.000000E+01 -7.105427E-15  1.776357E-15 -2.475942E-15 -1.954691E-15  2.150161E-15
         6   4  1.000000E+01  4.440892E-15  2.664535E-15 -1.107658E-15 -6.776264E-15  0.000000E+00
"""


# ----------------------------------------------------------------------------------------------- fixture builders

def kuhn_cube(L: float = 2.0):
    """A cube [0, L]^3 in six tetrahedra (Kuhn triangulation along the 000-111 diagonal) with straight-edge midside
    nodes in C3D10 order (test fixture): (nodes (27, 3), tets (6, 10))."""
    corners = np.array([[(i >> 0) & 1, (i >> 1) & 1, (i >> 2) & 1] for i in range(8)], dtype=np.float64) * L
    nodes, index, tets = [tuple(c) for c in corners], {}, []
    for perm in itertools.permutations(range(3)):
        path, cur = [0], 0
        for a in perm:
            cur |= 1 << a
            path.append(cur)
        row = list(path)
        for a, b in cf.EDGES:
            key = tuple(sorted((path[a], path[b])))
            if key not in index:
                index[key] = len(nodes)
                nodes.append(tuple(0.5 * (corners[key[0]] + corners[key[1]])))
            row.append(index[key])
        tets.append(row)
    X = np.array(nodes, dtype=np.float64)
    return X, cf.order_tet10(X, np.array(tets, dtype=np.int64))


def one_tet():
    """One straight-sided tet10 in C3D10 order with positive volume (test fixture)."""
    c = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 1.5]])
    mids = [0.5 * (c[a] + c[b]) for a, b in cf.EDGES]
    return np.vstack([c, mids]), np.arange(10, dtype=np.int64)[None, :]


def bc_dict(L: float = 2.0, *, supports=None, loads=None, window=0.5, envelope=True) -> dict:
    """A bc_from()-shaped dict for the cube [0, L]^3 (test input)."""
    return {"supports": supports if supports is not None else
            [{"name": "x0", "box": [0, 0, 0, 0, L, L], "dofs": [0]}, {"name": "y0", "box": [0, 0, 0, L, 0, L], "dofs": [1]},
             {"name": "z0", "box": [0, 0, 0, L, L, 0], "dofs": [2]}],
            "loads": loads if loads is not None else
            [{"name": "tip", "box": [L, 0, 0, L, L, L], "force": [40.0, 0.0, 0.0], "axis": "x", "case": "default"}],
            "material": {"E_MPa": 200000.0, "nu": 0.3, "rho_kg_mm3": 7.85e-6}, "fea_mm": 2 * window,
            "window_mm": window, "normal_min": cf.NORMAL_MIN,
            "origin_mm": [0.0, 0.0, 0.0] if envelope else None, "envelope_mm": [L, L, L] if envelope else None,
            "exclude_boxes": []}


def eff_fixture(**derived) -> dict:
    """An effective spec (test input) with the root-mount objective and constraints and tol 0.10."""
    d = {"fea_mm": 4.0, "tol": 0.10, "objective": {"output": "mass_kg", "sense": "min"},
         "constraints": [{"output": "vm_p99_MPa", "op": "<=", "limit": 60.0},
                         {"output": "umax_mm", "op": "<=", "limit": 0.035}],
         "grid": {"origin_mm": [0.0, 0.0, 0.0], "envelope_mm": [100.0, 100.0, 40.0]}}
    d.update(derived)
    return {"derived": d, "material": {"name": "test", "E_MPa": 70000.0, "nu": 0.33, "rho_kg_mm3": 2.67e-6,
                                       "source": "test input"},
            "supports": [{"name": "foot", "box": [0, 0, 0, 10, 10, 0], "dofs": [0, 1, 2]}],
            "loads": [{"name": "cup", "box": [0, 0, 40, 10, 10, 40], "force": [0, 0, -100.0], "axis": "z",
                       "case": "default"}]}


def write_fixture(d: Path, *, gz: bool = False) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for name, text in (("ccx.inp", FIXTURE_INP), ("ccx.dat", FIXTURE_DAT)):
        if gz:
            with open(d / f"{name}.gz", "wb") as raw, gzip.GzipFile(filename="", mode="wb", compresslevel=6,
                                                                    fileobj=raw, mtime=0) as g:
                g.write(text.encode("utf-8"))
        else:
            (d / name).write_bytes(text.encode("utf-8"))
    return d


# ------------------------------------------------------------------------------------------------------ pure numpy

class TestTet10(unittest.TestCase):
    def test_midside_nodes_placed_by_geometry_from_any_order(self):
        X, T = one_tet()
        rng = np.random.default_rng(7)
        for _ in range(20):
            perm = rng.permutation(6)
            raw = np.concatenate([T[0, :4], T[0, 4:][perm]])[None, :]
            got = cf.order_tet10(X, raw)
            self.assertEqual(got.tolist(), T.tolist())
            for k, (a, b) in enumerate(cf.EDGES):
                np.testing.assert_allclose(X[got[0, 4 + k]], 0.5 * (X[got[0, a]] + X[got[0, b]]), rtol=0, atol=0)

    def test_inverted_tet_gets_corners_1_and_2_swapped(self):
        X, T = one_tet()
        raw = T.copy()
        raw[0, [1, 2]] = raw[0, [2, 1]]                       # det < 0
        raw[0, 4:] = raw[0, 4:][[3, 0, 5, 1, 4, 2]]            # and the midside nodes shuffled
        got = cf.order_tet10(X, raw)
        c = X[got[0, :4]]
        self.assertGreater(float(np.linalg.det(np.stack([c[1] - c[0], c[2] - c[0], c[3] - c[0]]))), 0.0)
        self.assertEqual(sorted(got[0, :4].tolist()), [0, 1, 2, 3])
        for k, (a, b) in enumerate(cf.EDGES):
            np.testing.assert_array_equal(X[got[0, 4 + k]], 0.5 * (X[got[0, a]] + X[got[0, b]]))
        self.assertAlmostEqual(float(cf.tet_volumes(X, got)[0]), 3.0 * 2.0 * 1.5 / 6.0, places=14)

    def test_a_midside_node_off_its_edge_and_a_flat_tet_are_refused(self):
        X, T = one_tet()
        bent = X.copy()
        bent[4] += [0.0, 0.0, 0.01]                              # (0,1) midpoint lifted: a curved edge
        with self.assertRaises(ValueError):
            cf.order_tet10(bent, T)
        flat = X.copy()
        flat[3] = [1.0, 1.0, 0.0]                                # fourth corner in the base plane
        flat[4:] = [0.5 * (flat[a] + flat[b]) for a, b in cf.EDGES]
        with self.assertRaises(ValueError):
            cf.order_tet10(flat, T)


class TestBoundaryFaces(unittest.TestCase):
    def test_cube_in_six_tets(self):
        X, T = kuhn_cube(2.0)
        self.assertEqual(T.shape, (6, 10))
        self.assertAlmostEqual(float(cf.tet_volumes(X, T).sum()), 8.0, places=13)
        bf = cf.boundary_faces(X, T)
        self.assertEqual(len(bf["tri"]), 12)                     # 6 faces x 2 triangles
        self.assertAlmostEqual(float(bf["area"].sum()), 24.0, places=12)
        centre = np.array([1.0, 1.0, 1.0])
        for k in range(12):
            n, c = bf["normal"][k], bf["centroid"][k]
            self.assertAlmostEqual(float(np.linalg.norm(n)), 1.0, places=12)
            self.assertEqual(int(np.sum(np.abs(n) > 1 - 1e-12)), 1)   # axis-aligned
            self.assertGreater(float(np.dot(n, c - centre)), 0.0)        # outward
            a, b, cc = X[bf["tri"][k]]
            np.testing.assert_allclose(np.cross(b - a, cc - a) / np.linalg.norm(np.cross(b - a, cc - a)), n, atol=1e-12)
            for j, (p, q) in enumerate(((0, 1), (1, 2), (2, 0))):
                np.testing.assert_array_equal(X[bf["mid"][k, j]], 0.5 * (X[bf["tri"][k, p]] + X[bf["tri"][k, q]]))


class TestFaceSelection(unittest.TestCase):
    def setUp(self):
        self.X, self.T = kuhn_cube(2.0)
        self.bf = cf.boundary_faces(self.X, self.T)
        self.env = ([0.0, 0.0, 0.0], [2.0, 2.0, 2.0])

    def sel(self, box, a, w=0.5, env=True):
        o, e = self.env if env else (None, None)
        return cf._faces_on_plane(self.bf, box, a, w, o, e)

    def test_footprint(self):
        self.assertEqual(int(self.sel([2, 0, 0, 2, 2, 2], 0).sum()), 2)
        # half the face: only the triangles whose centroid lies in y <= 1 (inclusive extent)
        s = self.sel([2, 0, 0, 2, 1, 2], 0)
        self.assertTrue(np.all(self.bf["centroid"][s, 1] <= 1.0))
        self.assertEqual(int(s.sum()), 1)
        self.assertEqual(int(self.sel([2, 5, 5, 2, 6, 6], 0).sum()), 0)   # footprint off the part

    def test_window(self):
        self.assertEqual(int(self.sel([2.4, 0, 0, 2.4, 2, 2], 0, w=0.5, env=False).sum()), 2)
        self.assertEqual(int(self.sel([2.6, 0, 0, 2.6, 2, 2], 0, w=0.5, env=False).sum()), 0)

    def test_facing(self):
        # x = 0 is the envelope minimum: only faces with n_x <= -NORMAL_MIN, never the opposite face
        s = self.sel([0, 0, 0, 0, 2, 2], 0, w=5.0)
        self.assertEqual(int(s.sum()), 2)
        self.assertTrue(np.all(self.bf["normal"][s, 0] <= -cf.NORMAL_MIN))
        s = self.sel([2, 0, 0, 2, 2, 2], 0, w=5.0)
        self.assertTrue(np.all(self.bf["normal"][s, 0] >= cf.NORMAL_MIN))
        # an interior plane (not an envelope face) takes either facing; side faces (n_x = 0) never
        s = self.sel([1, 0, 0, 1, 2, 2], 0, w=5.0, env=False)
        self.assertEqual(int(s.sum()), 4)
        self.assertTrue(np.all(np.abs(self.bf["normal"][s, 0]) >= cf.NORMAL_MIN))


class TestMapping(unittest.TestCase):
    def setUp(self):
        self.X, self.T = kuhn_cube(2.0)

    def test_consistent_traction_sums_to_F(self):
        for F in ([40.0, 0.0, 0.0], [3.0, -4.0, 12.0]):
            m = cf.map_bcs(self.X, self.T, bc_dict(loads=[{"name": "tip", "box": [2, 0, 0, 2, 2, 2], "force": F,
                                                           "axis": "x", "case": "default"}],
                                                   supports=[{"name": "x0", "box": [0, 0, 0, 0, 2, 2], "dofs": [0, 1, 2]}]))
            cl = m["cload"]
            for d in range(3):
                self.assertLessEqual(abs(cl[cl[:, 1] == d, 2].sum() - F[d]), 1e-12 * max(1.0, abs(F[d])))
            corners = set(range(8))
            self.assertFalse(any(int(n) in corners for n in cl[:, 0]))   # corners carry nothing
            rec = m["loads"][0]
            self.assertEqual(rec["faces"], 2)
            self.assertAlmostEqual(rec["coverage"], 1.0, places=12)
            np.testing.assert_allclose(rec["traction_MPa"], np.asarray(F) / 4.0, rtol=1e-15)
            # each midside node of a face carries t A_f / 3 (A_f = 2 mm2): 1/3 of F/2 per face
            per = {}
            for n, d, v in cl:
                per.setdefault(int(n), np.zeros(3))[int(d)] += v
            shared = [n for n, v in per.items() if np.allclose(v, np.asarray(F) / 3.0)]
            self.assertEqual(len(shared), 1)                              # the diagonal's midside node, two faces

    def test_support_sets_union_of_dofs_and_conflicts(self):
        m = cf.map_bcs(self.X, self.T, bc_dict())
        names = [s["name"] for s in m["supports"]]
        self.assertEqual(names, ["x0", "y0", "z0"])
        owned = [set(s["node_ids"].tolist()) for s in m["supports"]]
        self.assertFalse(owned[0] & owned[1] or owned[0] & owned[2] or owned[1] & owned[2])
        self.assertGreater(m["supports"][1]["overlap_nodes"], 0)
        extra = {(int(n), d) for ids, dofs in m["extra_dofs"] for n in ids for d in dofs}
        self.assertIn((0, 1), extra)                                      # origin node: x0 owns it, y0 fixes y
        self.assertIn((0, 2), extra)
        # a load along a dof a support fixes is refused, along a free dof it is kept (patch-test edges)
        bad = bc_dict(loads=[{"name": "push", "box": [2, 0, 0, 2, 2, 2], "force": [0.0, 5.0, 0.0], "axis": "x",
                              "case": "default"}])
        with self.assertRaises(cf.MappingError) as cm:
            cf.map_bcs(self.X, self.T, bad)
        self.assertIn("is loaded by push along y, which support y0 fixes", str(cm.exception))
        self.assertEqual([s["name"] for s in cm.exception.partial["supports"]], ["x0", "y0", "z0"])

    def test_mapping_refusals(self):
        far = bc_dict(supports=[{"name": "nowhere", "box": [0, 5, 5, 0, 6, 6], "dofs": [0, 1, 2]}])
        with self.assertRaises(cf.MappingError) as cm:
            cf.map_bcs(self.X, self.T, far)
        self.assertEqual(str(cm.exception), "support nowhere: no surface of the part within 0.5 mm of x=0 inside its box")
        thin = bc_dict(loads=[{"name": "wide", "box": [2, 0, 0, 2, 4, 2], "force": [1.0, 0, 0], "axis": "x",
                               "case": "default"}])
        with self.assertRaises(cf.MappingError) as cm:
            cf.map_bcs(self.X, self.T, thin)
        self.assertIn("load wide: the selected surface covers 0.500 of its 8 mm2 footprint (minimum 0.9)",
                      str(cm.exception))
        nodal = cf.map_bcs(self.X, self.T, bc_dict(loads=[{"name": "pt", "box": [2, 2, 2, 2, 2, 2],
                                                            "force": [1.0, 2.0, 3.0], "axis": None, "case": "d"}]))
        self.assertEqual(nodal["loads"][0]["mode"], "nodal")
        self.assertEqual(nodal["cload"].tolist(), [[7.0, 0.0, 1.0], [7.0, 1.0, 2.0], [7.0, 2.0, 3.0]])

    def test_bc_from_names_like_the_voxel_solve(self):
        eff = eff_fixture()
        eff["supports"] = [{"box": [0, 0, 0, 1, 1, 0]}, {"name": "a", "box": [0, 0, 0, 1, 1, 0], "dofs": [2]},
                           {"name": "a", "box": [0, 0, 0, 1, 1, 0], "dofs": [0]}]
        bc = cf.bc_from(eff, {"stress_exclude": [[0, 0, 0, 1, 1, 1]]})
        self.assertEqual([s["name"] for s in bc["supports"]], ["support1", "a", "a#3"])
        self.assertEqual(bc["supports"][0]["dofs"], [0, 1, 2])
        self.assertEqual(bc["window_mm"], 2.0)
        self.assertEqual(bc["exclude_boxes"], [[0.0, 0.0, 0.0, 1.0, 1.0, 1.0]])


class TestNumbers(unittest.TestCase):
    def test_weighted_percentile_hand_worked(self):
        wp = cf.weighted_percentile
        self.assertEqual(wp([1, 2, 3, 4], [1, 1, 1, 1], 50), 2.0)     # cum 1 2 3 4, target 2(1-1e-12): index 1
        self.assertEqual(wp([1, 2, 3, 4], [1, 1, 1, 1], 50.0001), 3.0)
        self.assertEqual(wp([1, 2, 3, 4], [1, 1, 1, 1], 100), 4.0)
        self.assertEqual(wp([4, 1, 3, 2], [1, 1, 1, 1], 75), 3.0)     # unsorted input
        self.assertEqual(wp([10, 20, 30], [1, 2, 7], 50), 30.0)        # cum 1 3 10, target 5
        self.assertEqual(wp([10, 20, 30], [1, 2, 7], 25), 20.0)        # target 2.5
        self.assertEqual(wp([10, 20, 30], [1, 2, 7], 10), 10.0)        # target 1(1-1e-12) <= 1
        self.assertEqual(wp([5.0], [3.0], 99), 5.0)
        self.assertTrue(math.isnan(wp([], [], 99)))
        self.assertEqual(wp([2.0, 1.0, 2.0, 1.0], [1, 1, 1, 1], 50), 1.0)   # ties: stable

    def test_von_mises(self):
        self.assertAlmostEqual(float(cf.von_mises([10, 0, 0, 0, 0, 0])), 10.0, places=14)
        self.assertAlmostEqual(float(cf.von_mises([0, 0, 0, 1, 0, 0])), math.sqrt(3), places=14)
        self.assertAlmostEqual(float(cf.von_mises([5, 5, 5, 0, 0, 0])), 0.0, places=14)

    def test_deck_numbers_fit_ccx_fields(self):
        for x in (-5.148283889866434e-05, -0.021370211616158485, 1e-300, -1.2345678901234567e+200, 24.999862670898438):
            s = cf._fnum(x)
            self.assertLessEqual(len(s), 20, s)
            self.assertLessEqual(abs(float(s) - x), 5e-13 * abs(x))   # 13 significant digits at worst
        self.assertEqual(cf._fnum(2.0), "2.0")
        self.assertEqual(cf._f("1.234567-100"), 1.234567e-100)
        self.assertEqual(cf._f("-2.500000E+01"), -25.0)
        self.assertEqual(cf._f("1.0D+02"), 100.0)

    def test_estimate_and_bands(self):
        self.assertEqual(cf.estimate_dof(8000.0, 2.0), 37000)
        self.assertEqual(cf.estimate_dof(0.0, 2.0), 0)
        self.assertEqual(cf.bands(eff_fixture()), {"mass": 0.01, "stress": 0.15, "other": 0.10})
        self.assertEqual(cf.bands(eff_fixture(tol=0.2)), {"mass": 0.01, "stress": 0.2, "other": 0.2})


class TestFixtureResults(unittest.TestCase):
    """read_results, equilibrium and recompute on the embedded real ccx 2.23 deck and .dat (test fixture)."""

    def test_fixture_is_the_recorded_ccx_output(self):
        self.assertEqual(hashlib.sha256(FIXTURE_INP.encode()).hexdigest(), FIXTURE_INP_SHA256)
        self.assertEqual(hashlib.sha256(FIXTURE_DAT.encode()).hexdigest(), FIXTURE_DAT_SHA256)

    def test_read_results(self):
        with tempfile.TemporaryDirectory() as td:
            for gz in (False, True):
                res = cf.read_results(write_fixture(Path(td) / f"gz{gz}", gz=gz))
                X, T = kuhn_cube(2.0)
                np.testing.assert_array_equal(res["nodes"], X)
                np.testing.assert_array_equal(res["tets"], T)
                self.assertEqual(sorted(res["nsets"]), ["NALL", "SUP_1", "SUP_2", "SUP_3"])
                self.assertEqual(res["u"].shape, (27, 3))
                self.assertEqual(res["s_ip"].shape, (6, 4, 6))
                # closed form: u = (5e-5 x, -1.5e-5 y, -1.5e-5 z) mm, sxx = 10 MPa
                np.testing.assert_allclose(res["u"], X * [5e-5, -1.5e-5, -1.5e-5], rtol=0, atol=1e-12)
                np.testing.assert_allclose(res["s_ip"][:, :, 0], 10.0, rtol=1e-12)
                self.assertLess(float(np.abs(res["s_ip"][:, :, 1:]).max()), 1e-12)
                self.assertEqual(res["rf"]["SUP_1"].tolist(), [-40.0, 5.842754e-15, 7.548686e-15])
                self.assertAlmostEqual(float(res["cload"][:, 2].sum()), 40.0, places=12)

    def test_metrics_equilibrium_and_recompute(self):
        with tempfile.TemporaryDirectory() as td:
            d = write_fixture(Path(td) / "m001", gz=True)
            res = cf.read_results(d)
            mat = {"E_MPa": 200000.0, "nu": 0.3, "rho_kg_mm3": 7.85e-6}
            metrics, stress = cf.metrics_from(res, mat, [])
            self.assertAlmostEqual(metrics["mesh_volume_mm3"], 8.0, places=12)
            self.assertAlmostEqual(metrics["mass_kg"] / 6.28e-5, 1.0, places=12)
            self.assertAlmostEqual(metrics["compliance_Nmm"] / 4e-3, 1.0, places=12)     # F u_x = 40 x 1e-4
            self.assertAlmostEqual(metrics["umax_mm"] / math.sqrt(1e-8 + 2 * 9e-10), 1.0, places=12)
            for k in ("vm_max_MPa", "vm_p99_MPa", "vm_p95_MPa", "vm_max_ip_MPa"):
                self.assertAlmostEqual(metrics[k] / 10.0, 1.0, places=12)
            self.assertEqual(stress["elements_included"], 6)
            eq = cf.equilibrium_from(res, ["x0", "y0", "z0"])
            self.assertLessEqual(eq["rel_residual"], cf.EQUILIBRIUM_TOL)
            # ccx's RF total of y0 includes the 6.67 N applied at its x = 2 edge; the reaction is about 0
            self.assertEqual(eq["rf_totals_N"]["y0"][0], 6.666667)
            self.assertLess(abs(eq["per_support_N"]["y0"][0]), 1e-6)
            self.assertAlmostEqual(eq["per_support_N"]["x0"][0], -40.0, places=12)
            excl, _ = cf.metrics_from(res, mat, [[0, 0, 0, 2, 2, 2]])
            self.assertIsNone(excl["vm_p99_MPa"])
            # recompute() reads the gzipped files and conforming.json's material, supports and stress boxes
            rec = {"material": mat, "stress": {"exclude_boxes": []},
                   "bcs": {"supports": [{"name": "x0"}, {"name": "y0"}, {"name": "z0"}]}, "metrics": metrics}
            (d / "conforming.json").write_text(json.dumps(rec), encoding="utf-8")
            again = cf.recompute(d)
            self.assertEqual(again["metrics"], metrics)
            self.assertEqual(again["equilibrium"], eq)

    def test_unbalanced_or_broken_output_is_caught(self):
        with tempfile.TemporaryDirectory() as td:
            d = write_fixture(Path(td) / "bad")
            res = cf.read_results(d)
            res["rf"]["SUP_1"] = np.array([-39.0, 0.0, 0.0])                # test input: a missing newton
            self.assertGreater(cf.equilibrium_from(res, ["x0", "y0", "z0"])["rel_residual"], cf.EQUILIBRIUM_TOL)
            (d / "ccx.dat").write_text(FIXTURE_DAT.replace(" displacements (vx,vy,vz) for set NALL", " displ"),
                                       encoding="utf-8")
            with self.assertRaises(cf.ParseError):
                cf.read_results(d)
            lines = FIXTURE_DAT.splitlines()
            cut = [ln for ln in lines if not ln.startswith("         6   4")]
            (d / "ccx.dat").write_text("\n".join(cut) + "\n", encoding="utf-8")
            with self.assertRaises(cf.ParseError):
                cf.read_results(d)
            (d / "ccx.dat").write_text(FIXTURE_DAT.replace("set SUP_3", "set XXX"), encoding="utf-8")
            with self.assertRaises(cf.ParseError):
                cf.read_results(d)

    def test_deck_round_trip(self):
        X, T = kuhn_cube(2.0)
        m = cf.map_bcs(X, T, bc_dict())
        with tempfile.TemporaryDirectory() as td:
            info = cf.write_deck(Path(td) / "ccx.inp", X, T, m, {"E_MPa": 200000.0, "nu": 0.3},
                                 heading="aces golden test fixture: 2 mm cube, six C3D10, uniaxial patch test (40 N on x=2)")
            text = (Path(td) / "ccx.inp").read_text(encoding="utf-8")
            self.assertEqual(text, FIXTURE_INP)                             # the deck the fixture's ccx run read
            self.assertEqual(info, {"dof": 81, "linear_solver_card": "SPOOLES"})
            with mock.patch.object(cf, "SPOOLES_MAX_DOF", 80):
                info = cf.write_deck(Path(td) / "big.inp", X, T, m, {"E_MPa": 1.0, "nu": 0.3}, heading="h")
            self.assertEqual(info["linear_solver_card"], "ITERATIVE CHOLESKY")
            self.assertIn("*STATIC, SOLVER=ITERATIVE CHOLESKY", (Path(td) / "big.inp").read_text(encoding="utf-8"))


class TestCompare(unittest.TestCase):
    REF = {"mass_kg": 0.17, "mesh_volume_mm3": 63670.0, "compliance_Nmm": 800.0, "umax_mm": 0.030,
           "vm_p99_MPa": 50.0, "vm_p95_MPa": 40.0, "vm_max_MPa": 150.0}          # test input (voxel side)

    def rec(self, status="ok", **over):
        m = {"mass_kg": 0.1701, "mesh_volume_mm3": 63700.0, "compliance_Nmm": 840.0, "umax_mm": 0.0315,
             "vm_p99_MPa": 55.0, "vm_p95_MPa": 41.0, "vm_max_MPa": 120.0, "vm_max_ip_MPa": 200.0}
        m.update(over)
        return {"status": status, "message": "test input" if status != "ok" else "", "metrics": m}

    def test_rows_bands_and_pass(self):
        rows, passed, reasons = cf.compare(self.REF, self.rec(), eff_fixture())
        self.assertEqual([(r["output"], r["role"]) for r in rows],
                         [("mass_kg", "objective"), ("vm_p99_MPa", "constraint"), ("umax_mm", "constraint"),
                          ("compliance_Nmm", "gating"), ("vm_max_MPa", "reported"), ("vm_p95_MPa", "reported"),
                          ("mesh_volume_mm3", "reported")])
        self.assertEqual([r["band"] for r in rows], [0.01, 0.15, 0.10, 0.10, 0.15, 0.15, 0.01])
        r = {x["output"]: x for x in rows}
        self.assertAlmostEqual(r["vm_p99_MPa"]["rel_gap"], 0.1, places=14)
        self.assertAlmostEqual(r["vm_max_MPa"]["rel_gap"], -0.2, places=14)
        self.assertFalse(r["vm_max_MPa"]["agree"])                     # reported rows never gate
        self.assertTrue(r["umax_mm"]["meets_conforming"])
        self.assertIsNone(r["compliance_Nmm"]["meets_conforming"])
        self.assertTrue(passed)
        self.assertEqual(reasons, [])

    def test_failures(self):
        rows, passed, reasons = cf.compare(self.REF, self.rec(compliance_Nmm=900.0), eff_fixture())
        self.assertFalse(passed)
        self.assertEqual(reasons, ["compliance_Nmm 900 vs voxel 800: +12.5 % outside the 10 % band"])
        rows, passed, reasons = cf.compare(self.REF, self.rec(umax_mm=0.0331, vm_p99_MPa=61.0), eff_fixture(
            constraints=[{"output": "vm_p99_MPa", "op": "<=", "limit": 60.0}, {"output": "umax_mm", "op": "<=",
                                                                             "limit": 0.033}]))
        self.assertFalse(passed)
        self.assertIn("vm_p99_MPa 61 exceeds 60 on the conforming mesh", reasons)
        self.assertIn("umax_mm 0.0331 exceeds 0.033 on the conforming mesh", reasons)
        rows, passed, reasons = cf.compare(self.REF, self.rec(status="equilibrium"), eff_fixture())
        self.assertFalse(passed)
        self.assertEqual(reasons[0], "equilibrium: test input")
        self.assertTrue(all(x["rel_gap"] is not None for x in rows))   # metrics recorded, still compared
        rows, passed, reasons = cf.compare(self.REF, {"status": "ccx-not-found", "message": "ccx not found",
                                                      "metrics": {}}, eff_fixture())
        self.assertFalse(passed)
        self.assertEqual(reasons, ["not run: ccx-not-found: ccx not found"])
        self.assertTrue(all(x["conforming"] is None and x["agree"] is False for x in rows))

    def test_objective_already_gating(self):
        eff = eff_fixture(objective={"output": "compliance_Nmm", "sense": "min"},
                          constraints=[{"output": "mass_kg", "op": "<=", "limit": 0.2}])
        rows, passed, _ = cf.compare(self.REF, self.rec(), eff)
        self.assertEqual([r["output"] for r in rows],
                         ["compliance_Nmm", "mass_kg", "vm_max_MPa", "vm_p95_MPa", "mesh_volume_mm3"])
        self.assertTrue(passed)


class TestRunCheckWithoutTools(unittest.TestCase):
    """run_check's NOT_RUN and early FAILED paths: always a complete conforming.json, never an exception."""

    KEYS = ("schema", "case_id", "where", "status", "message", "started_utc", "finished_utc", "wall_s", "wall_s_mesh",
            "wall_s_solve", "wall_s_post", "host", "threads", "timeout_s", "max_dof", "source", "mesh", "bcs", "solver",
            "equilibrium", "metrics", "stress", "material", "files", "artifacts")

    def run_it(self, td, stl, **kw):
        eff = eff_fixture()
        out = Path(td) / "m001"
        rec = cf.run_check(stl, eff, {"stress_exclude": []}, out, case_id="m001", size_mm=2.0, where="desktop",
                           threads=1, **kw)
        on_disk = json.loads((out / "conforming.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, json.loads(json.dumps(rec)))
        self.assertEqual(tuple(rec), self.KEYS)
        self.assertEqual(rec["schema"], cf.SCHEMA)
        return rec

    def test_ccx_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            stl = cf.write_box_stl(Path(td) / "v001" / "part.stl", (0, 0, 0), (10, 10, 40))
            rec = self.run_it(td, stl, ccx=str(Path(td) / "no-such-ccx"))
            if not HAVE_GMSH:
                self.assertEqual(rec["status"], "gmsh-not-found")
            else:
                self.assertEqual(rec["status"], "ccx-not-found")
                self.assertIn("ccx not found at", rec["message"])
            self.assertIn(rec["status"], cf.NOT_RUN)
            self.assertEqual(rec["source"]["stl"], "v001/part.stl")
            self.assertEqual(rec["timeout_s"], cf.TIMEOUT_S["desktop"])
            self.assertEqual(rec["max_dof"], cf.MAX_DOF["desktop"])
            self.assertEqual(rec["material"], {"E_MPa": 70000.0, "nu": 0.33, "rho_kg_mm3": 2.67e-6})

    def test_gmsh_not_found(self):
        av = dict(_AV, gmsh=None, gmsh_reason="gmsh is not installed in this Python (test)", usable=False)
        with tempfile.TemporaryDirectory() as td, mock.patch.object(cf, "available", return_value=av):
            rec = self.run_it(td, cf.write_box_stl(Path(td) / "p.stl", (0, 0, 0), (1, 1, 1)))
            self.assertEqual(rec["status"], "gmsh-not-found")

    def usable(self):
        return {"gmsh": cf.GMSH_PIN, "gmsh_reason": "test", "ccx": "/test/ccx", "ccx_version": cf.CCX_PIN,
                "ccx_sha256": "0" * 64, "usable": True, "reason": "test input"}

    @unittest.skipUnless(HAVE_TRIMESH, "trimesh is not importable")
    def test_too_large_and_not_watertight_before_meshing(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(cf, "available", return_value=self.usable()):
            stl = cf.write_box_stl(Path(td) / "p.stl", (0, 0, 0), (10, 10, 40))
            rec = self.run_it(td, stl, max_dof=1000)
            self.assertEqual(rec["status"], "too-large")
            self.assertEqual(rec["mesh"]["dof_estimate"], cf.estimate_dof(4000.0, 2.0))
            self.assertIn("never coarsened silently", rec["message"])
            raw = (Path(td) / "p.stl").read_bytes()
            (Path(td) / "open.stl").write_bytes(raw[:80] + (11).to_bytes(4, "little") + raw[84:84 + 11 * 50])
            rec = self.run_it(td, Path(td) / "open.stl")
            self.assertEqual(rec["status"], "not-watertight")
            self.assertFalse(rec["source"]["watertight"])

    @unittest.skipUnless(HAVE_GMSH and HAVE_TRIMESH, "gmsh or trimesh is not importable")
    def test_a_mesh_over_spooles_max_dof_is_too_large_and_never_solved(self):
        """Review finding 1: above SPOOLES_MAX_DOF the deck would ask ccx for its iterative Cholesky solver, whose answer
        cannot pass EQUILIBRIUM_TOL, so run_check stops after meshing with too-large (NOT_RUN). The ccx here is a test
        stand-in that must never run (test input: the 10 x 10 x 40 mm box at 5 mm, SPOOLES_MAX_DOF patched low)."""
        with tempfile.TemporaryDirectory() as td, mock.patch.object(cf, "available", return_value=self.usable()), \
                mock.patch.object(cf, "SPOOLES_MAX_DOF", 30):
            stl = cf.write_box_stl(Path(td) / "p.stl", (0, 0, 0), (10, 10, 40))
            eff = eff_fixture()
            eff["derived"]["grid"] = {"origin_mm": [0.0, 0.0, 0.0], "envelope_mm": [10.0, 10.0, 40.0]}
            rec = cf.run_check(stl, eff, {"stress_exclude": []}, Path(td) / "m001", case_id="m001", size_mm=5.0,
                               where="desktop", threads=1)
            self.assertEqual(rec["status"], "too-large", rec["message"])
            self.assertIn(rec["status"], cf.NOT_RUN)
            self.assertGreater(rec["mesh"]["dof"], 30)
            self.assertLessEqual(rec["mesh"]["dof"], rec["max_dof"])        # under max_dof: the SPOOLES limit refused it
            self.assertTrue(rec["message"].startswith(f"the mesh has {rec['mesh']['dof']} DOF at 5 mm, over "
                                                      "SPOOLES_MAX_DOF 30: above it ccx would run its iterative "
                                                      "Cholesky solver"), rec["message"])
            self.assertEqual(sorted(rec["files"]), ["mesh.log", "mesh.npz", "tetmesh.json"])   # no deck, no ccx run
            self.assertIsNone(rec["solver"]["card"])
            self.assertIsNone(rec["wall_s_solve"])

    @unittest.skipUnless(HAVE_GMSH and HAVE_TRIMESH, "gmsh or trimesh is not importable")
    def test_bc_mapping_failed_keeps_the_partial_mapping(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(cf, "available", return_value=self.usable()):
            stl = cf.write_box_stl(Path(td) / "p.stl", (0, 0, 0), (10, 10, 40))
            eff = eff_fixture()
            eff["derived"]["grid"] = {"origin_mm": [0.0, 0.0, 0.0], "envelope_mm": [10.0, 10.0, 40.0]}
            eff["loads"][0]["box"] = [20, 20, 40, 30, 30, 40]               # off the part: nothing to load
            rec = cf.run_check(stl, eff, {"stress_exclude": []}, Path(td) / "m001", case_id="m001", size_mm=5.0,
                               where="desktop", threads=1)
            self.assertEqual(rec["status"], "bc-mapping-failed")
            self.assertEqual(rec["message"], "load cup: no surface of the part within 2 mm of z=40 inside its box")
            self.assertEqual(rec["bcs"]["supports"][0]["name"], "foot")
            self.assertEqual(rec["bcs"]["supports"][0]["mode"], "surface")
            self.assertEqual(rec["bcs"]["loads"][0]["faces"], None)
            self.assertEqual(sorted(rec["files"]), ["mesh.log", "mesh.npz", "tetmesh.json"])
            self.assertEqual(rec["mesh"]["route"], "remesh")

    def test_cli_usage(self):
        self.assertEqual(cf.main(["mesh", "--stl", "x.stl", "--out", "o", "--size", "0"]), 2)
        self.assertEqual(cf.main(["mesh", "--stl", "x.stl", "--out", "o", "--size", "1", "--attempts", "AX"]), 2)

    def test_available_reports_a_missing_ccx(self):
        av = cf.available(ccx=str(Path(tempfile.gettempdir()) / "no-such-ccx-binary"))
        self.assertIsNone(av["ccx"])
        self.assertFalse(av["usable"])
        self.assertIn("ccx not found at", av["reason"])
        self.assertEqual(set(av), {"gmsh", "gmsh_reason", "ccx", "ccx_version", "ccx_sha256", "usable", "reason"})


# ------------------------------------------------------------------------------------------------------------ gmsh

def run_mesher(stl: Path, out: Path, size: float, attempts: str, threads: int = 1) -> tuple[int, dict, dict]:
    p = subprocess.run([sys.executable, "-m", "aces_engineering.golden.conforming", "mesh", "--stl", str(stl), "--out",
                        str(out), "--size", repr(size), "--threads", str(threads), "--attempts", attempts],
                       capture_output=True, text=True, env=cf._child_env(), timeout=cf.MESH_TIMEOUT_S)
    rec = json.loads((out / "tetmesh.json").read_text(encoding="utf-8"))
    mesh = {}
    if rec["status"] == "ok":
        with np.load(out / "mesh.npz") as z:
            mesh = {"nodes": z["nodes"], "tets": z["tets"]}
    return p.returncode, rec, mesh


@unittest.skipUnless(HAVE_GMSH and HAVE_TRIMESH, "gmsh or trimesh is not importable here")
class TestMesher(unittest.TestCase):
    def check_tet10(self, mesh):
        X, T = mesh["nodes"], mesh["tets"]
        self.assertEqual(T.shape[1], 10)
        for k, (a, b) in enumerate(cf.EDGES):
            np.testing.assert_allclose(X[T[:, 4 + k]], 0.5 * (X[T[:, a]] + X[T[:, b]]), rtol=0, atol=1e-9)
        self.assertTrue(np.all(cf.tet_volumes(X, T) > 0))
        self.assertEqual(sorted(np.unique(T).tolist()), list(range(len(X))))   # every node used, numbered 0..N-1

    def test_cube_on_the_remesh_route(self):
        with tempfile.TemporaryDirectory() as td:
            stl = cf.write_box_stl(Path(td) / "cube.stl", (0, 0, 0), (10, 10, 10))
            rc, rec, mesh = run_mesher(stl, Path(td) / "o", 2.0, "A")
            self.assertEqual((rc, rec["status"], rec["route"], rec["algorithm_3d"]), (0, "ok", "remesh", 1))
            self.assertLessEqual(abs(rec["volume_rel_err"]), 0.005)
            self.assertGreater(rec["min_sicn"], 0.0)
            self.assertEqual(rec["dof"], 3 * rec["nodes"])
            self.check_tet10(mesh)

    def test_the_validation_bar_whose_blank_header_copy_gmsh_refused(self):
        """cudax scripts/golden_validate.py writes its 10 x 2 x 2 bar (CC-1) with struct, the -x face before the +x
        face (write_box_stl has them the other way round). trimesh re-exported it for gmsh with an all-zero header,
        and gmsh 4.15.2 refused that copy on every attempt (mesh-failed, measured on the integration stand-in). The
        mesher now writes its copies with STL_HEADER (test input: that bar, byte for byte)."""
        import struct

        p = [(0, 0, 0), (10, 0, 0), (10, 2, 0), (0, 2, 0), (0, 0, 2), (10, 0, 2), (10, 2, 2), (0, 2, 2)]
        quads = [((0, 3, 2, 1), (0, 0, -1)), ((4, 5, 6, 7), (0, 0, 1)), ((0, 1, 5, 4), (0, -1, 0)),
                 ((2, 3, 7, 6), (0, 1, 0)), ((0, 4, 7, 3), (-1, 0, 0)), ((1, 2, 6, 5), (1, 0, 0))]
        body = b""
        for (a, b, c, d), n in quads:
            for tri in ((a, b, c), (a, c, d)):
                body += struct.pack("<12fH", *(float(x) for x in n), *(float(x) for k in tri for x in p[k]), 0)
        with tempfile.TemporaryDirectory() as td:
            stl = Path(td) / "bar.stl"
            stl.write_bytes(b"golden_validate box (validation fixture)".ljust(80, b" ") + struct.pack("<I", 12) + body)
            rc, rec, mesh = run_mesher(stl, Path(td) / "o", 1.0, "ABCD")
            self.assertEqual((rc, rec["status"]), (0, "ok"), rec.get("message"))
            self.assertLessEqual(abs(rec["volume_rel_err"]), 0.005)
            self.check_tet10(mesh)
        self.assertFalse(cf.STL_HEADER.startswith(b"solid"))
        self.assertEqual(len(cf.STL_HEADER), 80)

    def test_icosphere_on_the_discrete_route(self):
        import trimesh

        with tempfile.TemporaryDirectory() as td:
            stl = Path(td) / "ico.stl"
            trimesh.creation.icosphere(subdivisions=3, radius=10.0).export(str(stl))
            rc, rec, mesh = run_mesher(stl, Path(td) / "o", 2.0, "C")
            self.assertEqual((rc, rec["status"], rec["route"]), (0, "ok", "discrete"))
            self.assertGreater(rec["min_sicn"], 0.0)
            self.assertGreater(rec["p01_sicn"], 0.0)
            self.check_tet10(mesh)

    def test_sphere_in_a_sphere_on_the_discrete_route(self):
        import trimesh

        with tempfile.TemporaryDirectory() as td:
            outer = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
            inner = trimesh.creation.icosphere(subdivisions=2, radius=5.0)
            inner.invert()
            stl = Path(td) / "hollow.stl"
            trimesh.util.concatenate([outer, inner]).export(str(stl))
            want = float(outer.volume + inner.volume)
            rc, rec, mesh = run_mesher(stl, Path(td) / "o", 2.0, "ACD")
            self.assertEqual((rc, rec["status"], rec["route"]), (0, "ok", "discrete"))
            self.assertEqual([a["attempt"] for a in rec["attempts"]], ["C"])   # A is never tried with a cavity
            self.assertEqual(rec["source"]["cavities"], 1)
            self.assertLessEqual(abs(rec["volume_mm3"] - want) / want, 0.01)
            self.check_tet10(mesh)

    def test_not_watertight_and_two_bodies(self):
        import trimesh

        with tempfile.TemporaryDirectory() as td:
            two = trimesh.util.concatenate([trimesh.creation.box(extents=(2, 2, 2)),
                                            trimesh.creation.box(extents=(2, 2, 2),
                                                                 transform=trimesh.transformations.translation_matrix((5, 0, 0)))])
            two.export(str(Path(td) / "two.stl"))
            rc, rec, _ = run_mesher(Path(td) / "two.stl", Path(td) / "o2", 1.0, "ABCD")
            self.assertEqual((rc, rec["status"]), (1, "multi-body"))
            box = trimesh.creation.box(extents=(2, 2, 2))
            box.update_faces(np.arange(len(box.faces) - 1))
            box.export(str(Path(td) / "open.stl"))
            rc, rec, _ = run_mesher(Path(td) / "open.stl", Path(td) / "o3", 1.0, "ABCD")
            self.assertEqual((rc, rec["status"]), (1, "not-watertight"))


# ------------------------------------------------------------------------------------------------------------- ccx

@unittest.skipUnless(HAVE_CCX and HAVE_TRIMESH, f"no usable ccx here ({CCX_WHY})")
class TestCcx(unittest.TestCase):
    def test_bar_patch_test(self):
        with tempfile.TemporaryDirectory() as td:
            out = cf.bar_patch_test(Path(td), threads=1)
            self.assertEqual(out["status"], "ok", out["record"]["message"])
            for name, c in out["checks"].items():
                self.assertLessEqual(c["rel"], 1e-6, f"{name}: got {c['got']!r}, want {c['want']!r}")
            self.assertLessEqual(out["equilibrium"], 1e-6)
            self.assertTrue(out["ok"])
            rec = out["record"]
            self.assertEqual(sorted(rec["files"]), sorted(cf.CASE_FILES))
            for name, sha in rec["files"].items():
                self.assertEqual(hashlib.sha256((Path(td) / "m001" / name).read_bytes()).hexdigest(), sha)
            self.assertEqual(cf.recompute(Path(td) / "m001")["metrics"], rec["metrics"])
            self.assertEqual(rec["solver"]["version"], cf.CCX_PIN)
            self.assertIn("spooles", rec["solver"]["linear_solver"].lower())

    def test_bar_patch_test_with_relative_paths(self):
        """The rig worker hands its adapter a run directory relative to its cwd (/work), so rigside passes relative
        paths on; the mesher child runs with cwd = out_dir, where those would name other files (a measured
        mesh-failed on the integration stand-in). run_check makes them absolute first (test input: the bar)."""
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            os.chdir(td)
            try:
                out = cf.bar_patch_test(Path("rel"), threads=1)
            finally:
                os.chdir(cwd)
            self.assertEqual(out["status"], "ok", out["record"]["message"])
            self.assertTrue(out["ok"])
            self.assertEqual(out["record"]["source"]["stl"], "bar.stl")

    def test_over_spooles_max_dof_the_real_ccx_is_never_asked(self):
        """Review finding 1 with the real ccx: the bar patch test with SPOOLES_MAX_DOF patched below its mesh's DOF is
        too-large (NOT_RUN) and ccx never runs, where the iterative Cholesky solver would have ended with an
        equilibrium residual far above EQUILIBRIUM_TOL (measured: see the module docstring of golden.conforming);
        test_bar_patch_test is the same bar under the real limit."""
        with tempfile.TemporaryDirectory() as td, mock.patch.object(cf, "SPOOLES_MAX_DOF", 300):
            out = cf.bar_patch_test(Path(td), threads=1)
            rec = out["record"]
            self.assertEqual(out["status"], "too-large", rec["message"])
            self.assertIsNone(out["ok"])                                    # not run: neither a pass nor a failure
            self.assertIn("over SPOOLES_MAX_DOF 300", rec["message"])
            self.assertGreater(rec["mesh"]["dof"], 300)
            self.assertIsNone(rec["solver"]["rc"])
            self.assertFalse((Path(td) / "m001" / "ccx.log").exists())
            self.assertFalse((Path(td) / "m001" / "ccx.inp.gz").exists())

    def test_cantilever(self):
        with tempfile.TemporaryDirectory() as td:
            stl = cf.write_box_stl(Path(td) / "beam.stl", (0, 0, 0), (100, 10, 10))
            eff = {"material": {"name": "test", "E_MPa": 200000.0, "nu": 0.3, "rho_kg_mm3": 7.85e-6, "source": "test"},
                   "supports": [{"name": "root", "box": [0, 0, 0, 0, 10, 10], "dofs": [0, 1, 2]}],
                   "loads": [{"name": "tip", "box": [100, 0, 0, 100, 10, 10], "force": [0.0, 0.0, -100.0],
                              "axis": "x", "case": "default"}],
                   "derived": {"fea_mm": 2.0, "tol": 0.10,
                               "grid": {"origin_mm": [0.0, 0.0, 0.0], "envelope_mm": [100.0, 10.0, 10.0]}}}
            rec = cf.run_check(stl, eff, {"stress_exclude": []}, Path(td) / "m001", case_id="m001", size_mm=2.0,
                               where="desktop", threads=2)
            self.assertEqual(rec["status"], "ok", rec["message"])
            res = cf.read_results(Path(td) / "m001")
            tip = np.abs(res["nodes"][:, 0] - 100.0) <= 1e-9
            uz = float(np.mean(res["u"][tip, 2]))
            self.assertLessEqual(abs(-uz - 0.20153) / 0.20153, 0.02, f"mean tip u_z {uz!r}")


if __name__ == "__main__":
    unittest.main()
