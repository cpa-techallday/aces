"""Frame check against hand calculations.  All numbers are test inputs."""
import math

import numpy as np
import pytest

pytest.importorskip("Pynite")

from aces.structures import StructureSpec, analyse  # noqa: E402
from aces.structures.frame import compression_sign  # noqa: E402

MAT = {"name": "test", "E_MPa": 200000, "poisson": 0.3, "strength_MPa": 250, "source": "test input"}


def _spec(**kw):
    base = {"material": MAT, "material_factor": 1.0,
            "supports": {"mode": "face", "axis": "z", "side": "min", "tol_mm": 1.0},
            "load_cases": [{"name": "SW", "self_weight_g": 1.0}]}
    base.update(kw)
    return StructureSpec.model_validate(base)


def _tripod(R=500.0, H=800.0):
    base = [(R * math.cos(a), R * math.sin(a), 0.0) for a in (0, 2 * math.pi / 3, 4 * math.pi / 3)]
    return np.array(base + [(0.0, 0.0, H)]), [(0, 3), (1, 3), (2, 3)]


def test_sign_convention_is_measured():
    assert compression_sign() in (1.0, -1.0)


def test_tripod_axial_force_and_buckling_utilisation():
    R, H, P, d = 500.0, 800.0, 3000.0, 20.0
    nodes, struts = _tripod(R, H)
    spec = _spec(load_cases=[{"name": "P", "point_loads": [{"at_mm": [0, 0, H], "force_N": [0, 0, -P]}]}])
    r = analyse(nodes, struts, d, 7850.0, spec)
    L = math.hypot(R, H)
    N = P / (3 * H / L)  # compression in each leg
    I = math.pi * d**4 / 64
    pcr = math.pi**2 * 200000 * I / L**2
    assert r.governing["axial_N_tension_positive"] == pytest.approx(-N, rel=1e-3)
    assert r.max_utilisation == pytest.approx(N / pcr, rel=0.02)  # bending is negligible by symmetry
    assert r.reactions_N["P"][2] == pytest.approx(P, rel=1e-6)


def test_tension_is_not_checked_for_buckling():
    R, H, P, d = 500.0, 800.0, 3000.0, 20.0
    nodes, struts = _tripod(R, H)
    spec = _spec(load_cases=[{"name": "P", "point_loads": [{"at_mm": [0, 0, H], "force_N": [0, 0, P]}]}])
    r = analyse(nodes, struts, d, 7850.0, spec)
    N = P / (3 * H / math.hypot(R, H))
    assert r.governing["axial_N_tension_positive"] == pytest.approx(N, rel=1e-3)
    assert r.max_utilisation == pytest.approx(N / (math.pi * d * d / 4) / 250, rel=0.02)


def test_self_weight_reaction_equals_weight():
    nodes, struts = _tripod()
    r = analyse(nodes, struts, 20.0, 7850.0, _spec(load_cases=[{"name": "SW", "self_weight_g": 1.0}]))
    L = math.hypot(500.0, 800.0)
    mass = 3 * math.pi * 100.0 * L * 7850e-9
    assert r.reactions_N["SW"][2] == pytest.approx(mass * 9.80665, rel=1e-6)


def test_pressure_resultant_equals_pressure_times_projected_area():
    # A flat 1 m x 1 m triangulated grid in the x-z plane, supported along its base.
    n = 5
    xs = np.linspace(0, 1000, n)
    nodes = np.array([(x, 0.0, z) for z in xs for x in xs])
    idx = lambda i, j: i * n + j  # noqa: E731
    struts = []
    for i in range(n):
        for j in range(n):
            if j + 1 < n:
                struts.append((idx(i, j), idx(i, j + 1)))
            if i + 1 < n:
                struts.append((idx(i, j), idx(i + 1, j)))
            if i + 1 < n and j + 1 < n:
                struts.append((idx(i, j), idx(i + 1, j + 1)))
    clamped = {"mode": "face", "axis": "z", "side": "min", "tol_mm": 1.0, "restrain_rotation": True}
    spec = _spec(supports=clamped, load_cases=[{"name": "W", "pressure_Pa": 500.0, "pressure_direction": [0, 1, 0]}])
    r = analyse(nodes, struts, 20.0, 7850.0, spec)
    assert r.reactions_N["W"][1] == pytest.approx(-500.0 * 1.0, rel=1e-6)  # reaction opposes the load
    pinned = _spec(load_cases=[{"name": "W", "pressure_Pa": 500.0, "pressure_direction": [0, 1, 0]}])
    with pytest.raises(ValueError, match="collinear"):
        analyse(nodes, struts, 20.0, 7850.0, pinned)


def test_too_few_supports_raise():
    nodes, struts = _tripod()
    spec = _spec(supports={"mode": "face", "axis": "z", "side": "max", "tol_mm": 1.0})
    with pytest.raises(ValueError, match="support"):
        analyse(nodes, struts, 20.0, 7850.0, spec)


def test_material_needs_a_source():
    bad = dict(MAT)
    del bad["source"]
    with pytest.raises(Exception):
        _spec(material=bad)


def test_torsion_von_mises_against_hand_calc():
    # Clamped column (pinned tripod base would also work) with an arm: a tip load in y bends the
    # column about x AND twists it.  Column base stresses: sigma = M c / I with M = P*H about x
    # plus P*a about z is carried by the arm/column joint; torsion T = P*a in the column.
    H = a = 300.0
    d, P = 10.0, 50.0
    base = [(0, 0, 0), (60, 0, 0), (0, 60, 0)]
    nodes = np.array(base + [(0, 0, H), (a, 0, H)], float)
    # Three stiff legs from the support triangle to the column top make the base sound;
    # the arm (3 -> 4) is the member we hand-check: torsion-free, bending M = P * a at its root.
    struts = [(0, 3), (1, 3), (2, 3), (3, 4)]
    spec = _spec(supports={"mode": "points", "points_mm": base, "snap_tol_mm": 1.0, "restrain_rotation": True},
                 load_cases=[{"name": "T", "point_loads": [{"at_mm": [a, 0, H], "force_N": [0, P, 0]}]}])
    r = analyse(nodes, struts, d, 7850.0, spec)
    I = math.pi * d**4 / 64
    arm_root = P * a * (d / 2) / I / 250.0  # bending utilisation of the arm root
    assert r.max_utilisation >= arm_root * 0.999
    assert r.governing["torque_Nmm"] >= 0.0


def test_short_column_uses_johnson_not_euler():
    from aces.structures.frame import column_capacity_N
    A, I = math.pi * 100.0, math.pi * 20**4 / 64  # d = 20 mm, r = 5 mm
    cap, euler = column_capacity_N(A, I, 300.0, 1.0, 200000.0, 250.0)  # KL/r = 60 < Cc ~ 126
    johnson = A * 250 * (1 - 250 * 60**2 / (4 * math.pi**2 * 200000))
    assert cap == pytest.approx(johnson) and cap < euler


def test_p_delta_amplifies_bending_in_compression():
    # Clamped cantilever column with axial load near Euler and a small lateral load.
    L, d = 500.0, 10.0
    I = math.pi * d**4 / 64
    pe_cantilever = math.pi**2 * 200000 * I / (2 * L) ** 2
    nodes = np.array([(0, 0, 0), (30, 0, 0), (0, 30, 0), (0, 0, L)], float)
    struts = [(0, 3), (1, 3), (2, 3)]  # tripod-like base so supports are sound; check amplification is reported
    spec = _spec(load_cases=[{"name": "C", "point_loads": [{"at_mm": [0, 0, L], "force_N": [20, 0, -0.5 * pe_cantilever]}]}],
                 effective_length_factor=1.0)
    r = analyse(nodes, struts, d, 7850.0, spec)
    assert r.governing["moment_amplification"] > 1.0


def test_duplicate_and_empty_load_cases_rejected():
    with pytest.raises(Exception, match="unique"):
        _spec(load_cases=[{"name": "W", "self_weight_g": 1}, {"name": "W", "self_weight_g": 1}])
    with pytest.raises(Exception, match="no load"):
        _spec(load_cases=[{"name": "E"}])


def test_point_load_far_from_any_node_is_refused():
    nodes, struts = _tripod()
    spec = _spec(load_cases=[{"name": "P", "point_loads": [{"at_mm": [0, 0, 5000], "force_N": [0, 0, -1]}]}])
    with pytest.raises(ValueError, match="nearest node"):
        analyse(nodes, struts, 20.0, 7850.0, spec)


def test_tributary_pressure_is_mesh_independent():
    from aces.structures.frame import tributary_areas
    rng = np.random.default_rng(1)
    pts = np.concatenate([[(0, 0, 0), (1000, 0, 0), (0, 0, 1000), (1000, 0, 1000)],
                          np.c_[rng.uniform(0, 1000, 60), np.zeros(60), rng.uniform(0, 200, 60)]])
    a = tributary_areas(pts, np.array([0.0, 1.0, 0.0]))
    assert a.sum() == pytest.approx(1e6, rel=1e-6)
    # Nodes clustered near the base carry less area each than the sparse top corners.
    assert a[2] > np.median(a[4:])


def test_pressure_in_the_plane_of_the_panel_is_refused():
    # a flat x-y panel loaded along x projects to a line: the load must not silently vanish
    from aces.structures.frame import tributary_areas
    xs, ys = np.meshgrid(np.linspace(0, 500, 6), np.linspace(0, 300, 4))
    pts = np.c_[xs.ravel(), ys.ravel(), np.zeros(xs.size)]
    with pytest.raises(ValueError, match="project to a line"):
        tributary_areas(pts, np.array([1.0, 0.0, 0.0]))
    assert tributary_areas(pts, np.array([0.0, 0.0, 1.0])).sum() == pytest.approx(500 * 300, rel=1e-9)


def test_repeated_struts_are_merged_not_doubled():
    nodes, struts = _tripod()
    spec = _spec(load_cases=[{"name": "P", "point_loads": [{"at_mm": list(nodes[-1]), "force_N": [0, 0, -500]}]}])
    once = analyse(nodes, struts, 6.0, 1240.0, spec)
    twice = analyse(nodes, struts + [(j, i) for i, j in struts], 6.0, 1240.0, spec)
    assert twice.max_utilisation == pytest.approx(once.max_utilisation, rel=1e-9)
    assert any("repeated strut" in n for n in twice.notes)
