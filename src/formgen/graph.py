"""Lattice graph: deduplicated nodes + classified struts, with print estimating."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np


@dataclass
class LatticeGraph:
    nodes: np.ndarray                      # (N,3) mm
    struts: list = field(default_factory=list)   # [(i, j, kind)]
    meta: dict = field(default_factory=dict)

    # -- construction ------------------------------------------------------
    @classmethod
    def empty(cls) -> "LatticeGraph":
        return cls(nodes=np.zeros((0, 3)), struts=[])

    def add_nodes(self, pts: np.ndarray) -> np.ndarray:
        """Append points, return their indices."""
        start = len(self.nodes)
        self.nodes = pts.copy() if start == 0 else np.vstack([self.nodes, pts])
        return np.arange(start, len(self.nodes))

    def add_strut(self, i: int, j: int, kind: str) -> None:
        if i != j:
            self.struts.append((int(i), int(j), kind))

    def dedupe(self, tol: float = 1e-3) -> None:
        """Merge coincident nodes and duplicate struts."""
        key = np.round(self.nodes / tol).astype(np.int64)
        _, first, inverse = np.unique(
            key, axis=0, return_index=True, return_inverse=True
        )
        self.nodes = self.nodes[first]
        remap = inverse
        seen, out = set(), []
        for i, j, kind in self.struts:
            a, b = int(remap[i]), int(remap[j])
            if a == b:
                continue
            k = (min(a, b), max(a, b))
            if k in seen:
                continue
            seen.add(k)
            out.append((a, b, kind))
        self.struts = out

    def prune_short(self, min_len: float) -> int:
        """Drop struts shorter than *min_len* mm; returns how many went.

        Offsetting a thin surface inwards (panel mode) can fold the inner skin
        onto itself and leave near-zero-length struts. They are not printable
        moves -- they are node blobs and wasted robot dwell time.
        """
        if min_len <= 0 or not self.struts:
            return 0
        keep, dropped = [], 0
        for i, j, kind in self.struts:
            if float(np.linalg.norm(self.nodes[i] - self.nodes[j])) < min_len:
                dropped += 1
                continue
            keep.append((i, j, kind))
        self.struts = keep
        return dropped

    # -- queries -----------------------------------------------------------
    def lengths(self) -> np.ndarray:
        idx = np.array([(i, j) for i, j, _ in self.struts])
        if len(idx) == 0:
            return np.zeros(0)
        return np.linalg.norm(self.nodes[idx[:, 0]] - self.nodes[idx[:, 1]], axis=1)

    def stats(self) -> dict:
        L = self.lengths()
        kinds = {}
        for _, _, k in self.struts:
            kinds[k] = kinds.get(k, 0) + 1
        return {
            "nodes": int(len(self.nodes)),
            "struts": int(len(self.struts)),
            "kinds": kinds,
            "total_length_m": float(L.sum() / 1000.0),
            "strut_len_mm": {
                "min": float(L.min()) if len(L) else 0.0,
                "mean": float(L.mean()) if len(L) else 0.0,
                "max": float(L.max()) if len(L) else 0.0,
            },
            "bbox_mm": {
                "min": self.nodes.min(axis=0).tolist() if len(self.nodes) else None,
                "max": self.nodes.max(axis=0).tolist() if len(self.nodes) else None,
            },
        }

    def estimate(
        self,
        bead_mm: float = 6.0,
        density_g_cm3: float = 1.10,
        print_speed_mm_s: float = 25.0,
        overhead_s_per_strut: float = 4.0,
    ) -> dict:
        """Material mass and wall-clock estimate — the quoting numbers."""
        L = self.lengths()
        total_mm = float(L.sum())
        area_mm2 = np.pi * (bead_mm / 2.0) ** 2
        vol_cm3 = total_mm * area_mm2 / 1000.0
        mass_kg = vol_cm3 * density_g_cm3 / 1000.0
        t_print = total_mm / print_speed_mm_s
        t_total = t_print + overhead_s_per_strut * len(L)
        return {
            "bead_mm": bead_mm,
            "density_g_cm3": density_g_cm3,
            "print_speed_mm_s": print_speed_mm_s,
            "material_volume_cm3": round(vol_cm3, 1),
            "material_mass_kg": round(mass_kg, 3),
            "extrusion_time_h": round(t_print / 3600.0, 2),
            "total_time_h": round(t_total / 3600.0, 2),
        }

    # -- connectivity ------------------------------------------------------
    def components(self):
        """Connected components of the strut graph, as lists of node indices."""
        n = len(self.nodes)
        parent = list(range(n))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for i, j, _ in self.struts:
            ra, rb = find(i), find(j)
            if ra != rb:
                parent[ra] = rb
        groups = {}
        for k in range(n):
            groups.setdefault(find(k), []).append(k)
        return sorted(groups.values(), key=len, reverse=True)

    def stitch_components(self, kind: str = "stitch", max_span: float = None):
        """Tie separated components together at their closest node pairs.

        Bodies that physically intersect but were meshed separately (a roof
        sitting on a wall, a rib buried in a shell) come through as islands in
        the strut graph. The print sequencer can only reach struts anchored to
        the ground, so those islands are all reported unreachable and get no
        print order. This links them -- greedily, nearest pair first, one strut
        per merge, which is a minimum spanning tree over the components -- so
        the whole structure is one buildable network.

        Returns the list of struts added as (i, j, distance).
        """
        comps = self.components()
        if len(comps) < 2:
            return []
        from scipy.spatial import cKDTree

        # start from the component holding the lowest node: that is the one
        # standing on the ground, so growing outwards from it keeps every
        # addition anchored
        zmin_of = [float(self.nodes[c][:, 2].min()) for c in comps]
        start = int(np.argmin(zmin_of))
        merged = list(comps[start])
        pending = [list(c) for k, c in enumerate(comps) if k != start]
        added = []

        while pending:
            tree = cKDTree(self.nodes[merged])
            best = None
            for pi, comp in enumerate(pending):
                d, idx = tree.query(self.nodes[comp], k=1)
                k = int(np.argmin(d))
                if best is None or d[k] < best[0]:
                    best = (float(d[k]), pi, int(comp[k]), int(merged[int(idx[k])]))
            dist, pi, node_new, node_old = best
            if max_span is not None and dist > max_span:
                break
            self.add_strut(node_old, node_new, kind)
            added.append((node_old, node_new, round(dist, 2)))
            merged.extend(pending.pop(pi))
        return added

    # -- io ----------------------------------------------------------------
    def to_dict(self, order: list | None = None) -> dict:
        return {
            "format": "formgen-lattice/1",
            "units": "mm",
            "meta": self.meta,
            "stats": self.stats(),
            "nodes": np.round(self.nodes, 4).tolist(),
            "struts": [[i, j, k] for i, j, k in self.struts],
            "print_order": order if order is not None else [],
        }

    def save_json(self, path: str, order: list | None = None) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(order), f)
