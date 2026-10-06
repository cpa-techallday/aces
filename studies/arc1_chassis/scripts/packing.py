"""ARC-1 :: what actually fits inside the lattice.

Voxelises the chassis interior, subtracts every rod plus a clearance, and
reports the largest axis-aligned box that fits in each bay. This is the answer
to "can it take an inboard motor, or does it need hub drives" - measured, not
estimated.

Run:  python scripts/packaging.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import trimesh
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad" /
                __import__("os").environ.get("ARC1_PARAMS",
                                             "params.json")).read_text())
OUT = ROOT / P["output"]["dir"]

GRID = 12.0          # mm voxel
CLEAR = 8.0          # mm clearance from any rod centreline beyond its radius

# A bay's x-range can be overridden per variant: the rotary rear deck only
# reaches full height aft of ~1900, and a box search over the ramp-up region
# is limited by its shallow end, not by the bay.
BAYS = {k: tuple(v) for k, v in P.get("bays", {
    "nose / attenuator":   (60, 480),
    "footwell / pedals":   (480, 900),
    "cockpit":             (900, 1640),
    "engine / battery":    (1640, 2300),
    "gearbox / rear":      (2300, 2620),
}).items()}

# real hardware, for the verdict
HARDWARE = [
    ("13B-REW rotary CORE",          380, 400, 450, 125.0, "206 kW, bare block, wet sump"),
    ("13B-REW CORE, dry sump",       380, 400, 370, 120.0, "no pan - normal for a race rotary"),
    ("13B Renesis CORE",             370, 390, 440, 120.0, "175 kW, bare block"),
    ("Emrax 188 axial-flux",        188, 188,  77,  7.0, "~52 kW peak"),
    ("Emrax 208 axial-flux",        208, 208,  85,  9.3, "~80 kW peak"),
    ("Emrax 228 axial-flux",        228, 228,  86, 12.9, "~124 kW peak"),
    ("Emrax 268 axial-flux",        268, 268,  91, 20.3, "~200 kW peak"),
    ("2x Emrax 228 side by side",   228, 470,  86, 25.8, "~248 kW peak"),
    ("FSAE 7 kWh accumulator",      420, 380, 300, 62.0, "typical segmented pack"),
    ("20 kWh pack (road/track)",    900, 500, 220, 145.0, "pouch modules"),
]


def fits_anywhere(free, dims, grid):
    """Can a solid box of `dims` mm sit anywhere in the free voxels, in any
    axis orientation? Uses a 3D summed-area table so every candidate position
    is an O(1) lookup. This is the question - 'largest box by volume' can
    return a long thin slot that no engine will ever go in."""
    import itertools
    S = np.zeros(np.array(free.shape) + 1, dtype=np.int32)
    S[1:, 1:, 1:] = np.cumsum(np.cumsum(np.cumsum(
        free.astype(np.int32), 0), 1), 2)

    def box_sum(i0, i1, j0, j1, k0, k1):
        return (S[i1, j1, k1] - S[i0, j1, k1] - S[i1, j0, k1] - S[i1, j1, k0]
                + S[i0, j0, k1] + S[i0, j1, k0] + S[i1, j0, k0]
                - S[i0, j0, k0])

    for perm in set(itertools.permutations(dims)):
        n = [int(np.ceil(d / grid)) for d in perm]
        if any(n[d] > free.shape[d] for d in range(3)):
            continue
        need = n[0] * n[1] * n[2]
        for i in range(free.shape[0] - n[0] + 1):
            for j in range(free.shape[1] - n[1] + 1):
                for k in range(free.shape[2] - n[2] + 1):
                    if box_sum(i, i + n[0], j, j + n[1],
                               k, k + n[2]) == need:
                        return True, perm, (i, j, k)
    return False, None, None


def largest_box(free, gx, gy, gz):
    """Largest axis-aligned box of True voxels. Returns (vol, (i0,i1,j0,j1,k0,k1))."""
    nx, ny, nz = free.shape
    best = (0, None)
    for i0 in range(nx):
        acc = np.ones((ny, nz), bool)
        for i1 in range(i0, nx):
            acc &= free[i1]
            if not acc.any():
                break
            a, rect = _largest_rect(acc)
            if a == 0:
                continue
            vol = a * (i1 - i0 + 1)
            if vol > best[0]:
                best = (vol, (i0, i1, *rect))
    return best


def _largest_rect(mask):
    """Largest all-True rectangle in a 2D boolean array (histogram method)."""
    ny, nz = mask.shape
    h = np.zeros(nz, int)
    best, rect = 0, None
    for j in range(ny):
        h = np.where(mask[j], h + 1, 0)
        stack = []
        for k in range(nz + 1):
            cur = h[k] if k < nz else 0
            start = k
            while stack and stack[-1][1] > cur:
                s, hh = stack.pop()
                area = hh * (k - s)
                if area > best:
                    best = area
                    rect = (j - hh + 1, j, s, k - 1)
                start = s
            stack.append((start, cur))
    return best, rect


def main():
    env_path = OUT / "arc1_envelope.stl"
    if not env_path.exists():  # fall back to the committed copy of the Fusion export
        env_path = ROOT / "cad" / "arc1_envelope.stl"
    env = trimesh.load(env_path)
    nodes = np.load(OUT / "micro_nodes.npy")
    rods = json.loads((OUT / "micro_rods.json").read_text())["rods"]
    rod_r = P["process"]["rod_diameter_mm"] / 2.0
    ball_r = P["process"]["node_ball_dia_mm"] / 2.0

    # sample points along every rod so a KD-tree can answer "how far from metal"
    samples = [nodes]
    for a, b, _, _ in rods:
        p0, p1 = nodes[a], nodes[b]
        n = max(2, int(np.linalg.norm(p1 - p0) / 10.0))
        t = np.linspace(0, 1, n)[:, None]
        samples.append(p0 * (1 - t) + p1 * t)
    pts = np.vstack(samples)
    tree = cKDTree(pts)
    print(f"metal sampled at {len(pts):,} points "
          f"({len(rods):,} rods, {len(nodes):,} nodes)")

    lo = np.array([0.0, -330.0, 20.0])
    hi = np.array([2620.0, 330.0, 520.0])
    xs = np.arange(lo[0], hi[0], GRID)
    ys = np.arange(lo[1], hi[1], GRID)
    zs = np.arange(lo[2], hi[2], GRID)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    grid = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    print(f"grid {len(xs)}x{len(ys)}x{len(zs)} = {len(grid):,} voxels "
          f"at {GRID:.0f} mm")

    inside = env.contains(grid)
    d, _ = tree.query(grid, k=1)
    free = inside & (d > (rod_r + ball_r + CLEAR))
    free3 = free.reshape(X.shape)
    vox_l = (GRID ** 3) / 1e6
    print(f"inside envelope {inside.sum():,} voxels, "
          f"clear of metal {free.sum():,} "
          f"({free.sum()*vox_l:,.0f} L total)\n")

    print(f"{'bay':<22} {'free vol':>9}   largest clear box (mm)")
    results = {}
    for name, (x0, x1) in BAYS.items():
        sel = (xs >= x0) & (xs < x1)
        sub = free3[sel]
        if not sub.any():
            print(f"{name:<22} {'-':>9}")
            continue
        vol_l = sub.sum() * vox_l
        v, idx = largest_box(sub, xs[sel], ys, zs)
        if idx is None:
            print(f"{name:<22} {vol_l:>7.1f} L   none")
            continue
        i0, i1, j0, j1, k0, k1 = idx
        bx = (i1 - i0 + 1) * GRID
        by = (j1 - j0 + 1) * GRID
        bz = (k1 - k0 + 1) * GRID
        xa = xs[sel][i0]
        print(f"{name:<22} {vol_l:>7.1f} L   "
              f"{bx:>4.0f} x {by:>3.0f} x {bz:>3.0f}  "
              f"@ x={xa:.0f}, y={ys[j0]:+.0f}..{ys[j1]:+.0f}, "
              f"z={zs[k0]:.0f}..{zs[k1]:.0f}")
        results[name] = {"free_L": round(vol_l, 1),
                         "box_mm": [bx, by, bz],
                         "at": [float(xa), float(ys[j0]), float(zs[k0])]}

    # ---- verdict ----------------------------------------------------
    eb = results.get("engine / battery")
    print(f"\nWHAT FITS IN THE ENGINE / BATTERY BAY "
          f"({eb['box_mm'][0]:.0f} x {eb['box_mm'][1]:.0f} x "
          f"{eb['box_mm'][2]:.0f} mm clear)")
    L, W, H = eb["box_mm"]
    sel = (xs >= BAYS["engine / battery"][0]) & (xs < BAYS["engine / battery"][1])
    sub = free3[sel]
    for nm, a, b, c, kg, note in HARDWARE:
        ok, perm, _ = fits_anywhere(sub, (a, b, c), GRID)
        print(f"  [{'FITS' if ok else ' no '}] {nm:<28} "
              f"{a:>3.0f}x{b:>3.0f}x{c:>3.0f} mm  {kg:>5.1f} kg   {note}")

    (OUT / "packaging.json").write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT/'packaging.json'}")


if __name__ == "__main__":
    main()
