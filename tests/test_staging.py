"""Tests for the STL→USD staging bridge (no Isaac Sim needed)."""
from __future__ import annotations

import struct

import numpy as np
import pytest

from aces.isaac import staging


def _write_binary_stl(path, tris):
    """tris: (n, 3, 3) array of triangle corners."""
    n = len(tris)
    header = b"test stl" + b"\0" * (80 - 8)
    buf = bytearray(header + struct.pack("<I", n))
    for t in tris:
        buf += struct.pack("<3f", 0.0, 0.0, 1.0)          # normal
        buf += struct.pack("<9f", *t.reshape(-1))          # 3 vertices
        buf += struct.pack("<H", 0)                        # attr
    path.write_bytes(bytes(buf))


def test_read_binary_stl_dedupes(tmp_path):
    # two triangles of a unit square share 2 corners: 8 stored corners -> 4 unique
    tris = np.array([
        [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
        [[0, 0, 0], [1, 1, 0], [0, 1, 0]],
    ], dtype=np.float64)
    p = tmp_path / "sq.stl"
    _write_binary_stl(p, tris)
    verts, faces = staging.read_stl(p)
    assert verts.shape == (4, 3)
    assert faces.shape == (2, 3)
    # area check via indices: both triangles reference valid verts
    assert faces.max() == 3


def test_read_ascii_stl(tmp_path):
    p = tmp_path / "tri.stl"
    p.write_text(
        "solid t\nfacet normal 0 0 1\nouter loop\n"
        "vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
        "endloop\nendfacet\nendsolid t\n", encoding="utf-8")
    verts, faces = staging.read_stl(p)
    assert verts.shape == (3, 3)
    assert faces.shape == (1, 3)


def test_stage_stl_scales_mm_to_m(tmp_path):
    tris = np.array([[[0, 0, 0], [10, 0, 0], [0, 10, 0]]], dtype=np.float64)  # 10 mm legs
    src = tmp_path / "t.stl"
    _write_binary_stl(src, tris)
    out = tmp_path / "t.usda"
    stats = staging.stage_stl(src, out)
    assert stats["triangles"] == 1
    assert stats["size_m"] == [0.01, 0.01, 0.0]  # mm -> m
    text = out.read_text(encoding="utf-8")
    assert "#usda 1.0" in text
    assert 'defaultPrim = "mesh"' in text
    assert "xformOp:scale" in text
    assert "faceVertexCounts = [3]" in text


def test_stage_stl_rejects_garbage(tmp_path):
    p = tmp_path / "bad.stl"
    p.write_bytes(b"nope")
    with pytest.raises(ValueError):
        staging.read_stl(p)
