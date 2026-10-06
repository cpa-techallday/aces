"""ARC-1 :: turn the rod list into something the cell can actually build.

Rod cut list, module split against the robot's usable build cube, and a
build ORDER that never asks the robot to weld a rod whose node does not yet
exist (FrameFab-lite: a rod is placeable when at least one end is anchored).

Run:  python scripts/buildplan.py
"""
from __future__ import annotations

import heapq
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad" /
                __import__("os").environ.get("ARC1_PARAMS",
                                             "params.json")).read_text())
OUT = ROOT / P["output"]["dir"]
PR, ROB, MOD = P["process"], P["robot"], P["modules"]


def sequence(nodes, rods, ground_tol=25.0):
    """Greedy buildable order. Returns steps and the count of rods that could
    only be reached from an already-free-floating node (needing a fixture)."""
    z = nodes[:, 2]
    zmin = float(z.min())
    anchored = set(np.where(z <= zmin + ground_tol)[0].tolist())

    adj = defaultdict(list)
    for s, (i, j, _, _) in enumerate(rods):
        adj[i].append(s)
        adj[j].append(s)

    def pri(s):
        i, j, _, _ = rods[s]
        a, b = nodes[i], nodes[j]
        return (round(max(a[2], b[2]), 2),
                round(float(np.linalg.norm(a - b)), 2), s)

    done = [False] * len(rods)
    ready, pushed = [], set()

    def push(n):
        for s in adj[n]:
            if not done[s] and s not in pushed:
                heapq.heappush(ready, (*pri(s), s))
                pushed.add(s)

    for n in anchored:
        push(n)

    steps = []
    while ready:
        *_, s = heapq.heappop(ready)
        if done[s]:
            continue
        i, j, kind, cls = rods[s]
        ai, aj = i in anchored, j in anchored
        if not (ai or aj):
            pushed.discard(s)
            continue
        src, dst = (i, j) if ai else (j, i)
        done[s] = True
        steps.append((s, src, dst))
        if dst not in anchored:
            anchored.add(dst)
            push(dst)
        push(src)
    return steps, sum(1 for d in done if not d)


def main():
    nodes = np.load(OUT / "micro_nodes.npy")
    hub = np.load(OUT / "micro_node_hub.npy")
    rods = [tuple(r) for r in json.loads(
        (OUT / "micro_rods.json").read_text())["rods"]]

    L = np.array([np.linalg.norm(nodes[a] - nodes[b]) for a, b, _, _ in rods])

    # ---- cut list: round to 1 mm, the practical shear increment ----
    cut = Counter(int(round(x)) for x in L)
    print(f"ROD CUT LIST  ({len(rods):,} rods, "
          f"{L.sum()/1000:,.0f} m of {PR['rod_diameter_mm']} mm rod)")
    print(f"  {len(cut)} distinct lengths, "
          f"{L.min():.0f}-{L.max():.0f} mm")
    bands = [(0, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 90)]
    for lo, hi in bands:
        n = int(((L >= lo) & (L < hi)).sum())
        if n:
            print(f"    {lo:>3}-{hi:<3} mm : {n:>6,} rods "
                  f"({n/len(rods):>5.1%})  {'#' * int(40*n/len(rods))}")
    top = cut.most_common(8)
    print("  most common: " + ", ".join(f"{k}mm x{v:,}" for k, v in top))

    # ---- module split against the robot build cube ----
    cube = ROB["usable_build_cube_mm"]
    splits = MOD["split_x_mm"]
    edges = [-1e9] + list(splits) + [1e9]
    print(f"\nMODULE SPLIT  (robot {ROB['model']}, "
          f"{ROB['reach_mm']:.0f} mm reach, {cube:.0f} mm usable cube)")
    mod_of_node = np.digitize(nodes[:, 0], splits)
    for m in range(len(edges) - 1):
        sel = mod_of_node == m
        if not sel.any():
            continue
        pts = nodes[sel]
        ext = pts.max(axis=0) - pts.min(axis=0)
        nr = sum(1 for a, b, _, _ in rods
                 if mod_of_node[a] == m and mod_of_node[b] == m)
        fits = "OK " if max(ext) <= cube else "OVER"
        print(f"  module {m}: x {pts[:,0].min():>6.0f}..{pts[:,0].max():<6.0f} "
              f"envelope {ext[0]:>4.0f}x{ext[1]:>4.0f}x{ext[2]:>4.0f} mm  "
              f"{int(sel.sum()):>5,} nodes {nr:>6,} rods   [{fits}]")
    cross = sum(1 for a, b, _, _ in rods if mod_of_node[a] != mod_of_node[b])
    print(f"  {cross:,} rods cross a module boundary -> "
          f"{MOD['joint']}")

    # ---- build order ----
    steps, unreachable = sequence(nodes, rods)
    print(f"\nBUILD ORDER")
    print(f"  {len(steps):,} of {len(rods):,} rods placeable bottom-up "
          f"from the platform")
    print(f"  {unreachable:,} need a temporary fixture "
          f"({unreachable/len(rods):.1%})")

    cyc = PR["rod_place_cycle_s"] + 2 * PR["arc_cycle_s"]
    hrs = len(rods) * cyc / 3600.0
    ball_h = len(nodes) * PR["node_ball_deposit_s"] / 3600.0
    print(f"\nCELL TIME  (1 robot, {cyc:.1f} s per rod, "
          f"{PR['node_ball_deposit_s']:.0f} s per node ball)")
    print(f"  rod place + 2 arcs : {hrs:>6,.0f} h")
    print(f"  node ball deposit  : {ball_h:>6,.0f} h")
    print(f"  TOTAL              : {hrs+ball_h:>6,.0f} h "
          f"({(hrs+ball_h)/24:.1f} days continuous, "
          f"{(hrs+ball_h)/4:.0f} h with 4 robots)")
    print(f"  {int(hub.sum()):,} nodes ({hub.mean():.1%}) are machined hubs, "
          f"fitted before their rods")

    (OUT / "buildplan.json").write_text(json.dumps({
        "cut_list": {str(k): v for k, v in sorted(cut.items())},
        "rods": len(rods), "nodes": int(len(nodes)),
        "rod_total_m": float(L.sum() / 1000.0),
        "sequence_placeable": len(steps),
        "needs_fixture": int(unreachable),
        "cross_module_rods": int(cross),
        "hours_single_robot": round(hrs + ball_h, 1),
        "machined_hubs": int(hub.sum()),
        "order": [[int(s), int(a), int(b)] for s, a, b in steps[:50000]],
    }))
    print(f"\nwrote {OUT/'buildplan.json'}")


if __name__ == "__main__":
    main()
