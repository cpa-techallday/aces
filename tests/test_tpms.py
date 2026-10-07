import pytest

from aces.domains.hx import tpms

# Mid-surface area per unit cell (a = 1) of the exact minimal surfaces, from the TPMS
# literature: Schoen gyroid 3.0919, Schwarz D 3.8378, Schwarz P 2.3451.  The level-set
# approximations used here are not exact minimal surfaces, so allow 1 %.
LITERATURE_AREA = {"gyroid": 3.0919, "diamond": 3.8378, "primitive": 2.3451}


@pytest.mark.parametrize("kind", tpms.KINDS)
def test_mid_surface_area_matches_literature(kind):
    assert tpms.mid_surface_area(kind, 96) == pytest.approx(LITERATURE_AREA[kind], rel=0.01)


@pytest.mark.parametrize("kind", tpms.KINDS)
def test_thin_wall_splits_cell_into_equal_networks(kind):
    u = tpms.unit_cell(kind, 0.02, 64)
    a, b = u.fluid_fraction
    assert a == pytest.approx(b, abs=0.01)
    assert u.solid_fraction + a + b == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("kind", tpms.KINDS)
def test_thin_wall_solid_fraction_is_area_times_thickness(kind):
    r = 0.04
    u = tpms.unit_cell(kind, r, 96)
    assert u.solid_fraction == pytest.approx(r * tpms.mid_surface_area(kind), rel=0.08)


def test_solid_fraction_grows_with_wall_ratio():
    fr = [tpms.unit_cell("gyroid", r, 48).solid_fraction for r in (0.05, 0.1, 0.2, 0.3)]
    assert fr == sorted(fr)


def test_resolution_convergence():
    coarse = tpms.unit_cell("gyroid", 0.1, 48)
    fine = tpms.unit_cell("gyroid", 0.1, 128)
    assert coarse.solid_fraction == pytest.approx(fine.solid_fraction, rel=0.02)
    assert coarse.area_density_a[0] == pytest.approx(fine.area_density_a[0], rel=0.01)


def test_sample_mesh_is_closed_and_matches_measured_volume():
    a, r, n = 0.004, 0.1, 2
    verts, faces = tpms.sample_mesh("gyroid", a, r * a, n_cells=n, region="solid", resolution=40)
    expected = tpms.unit_cell("gyroid", r, 64).solid_fraction * (n * a) ** 3
    assert tpms.mesh_volume(verts, faces) == pytest.approx(expected, rel=0.02)


def test_stl_round_trip(tmp_path):
    verts, faces = tpms.sample_mesh("primitive", 0.005, 0.0005, n_cells=1, resolution=24)
    p = tpms.write_stl(tmp_path / "s.stl", verts, faces)
    assert p.stat().st_size == 84 + 50 * len(faces)


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        tpms.unit_cell("lidinoid", 0.1)


def test_tabulated_mean_wall_is_never_below_the_minimum():
    """L8: both come from the same normal-direction measurement, so the mean can't fall below the minimum."""
    table = tpms._table()
    assert table, "precomputed table missing; run scripts/build_tpms_table.py"
    for rows in table["kinds"].values():
        for r in rows:
            assert r["wall_mean_over_a"] >= r["wall_min_over_a"]
    for kind in tpms.KINDS:  # interpolated between rows as well
        m = tpms.manufacturability(kind, 0.137)
        assert m.wall_mean_over_a >= m.wall_min_over_a
