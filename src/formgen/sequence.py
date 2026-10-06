"""Order struts for freeform spatial printing (FrameFab-lite greedy).

Rule: a strut may print when at least one endpoint is *anchored* — on the
build platform (z within ground_tol of the lattice minimum) or already part
of the printed structure. We always extrude FROM the anchored endpoint TO the
free one. Among printable struts we prefer the one whose highest endpoint is
lowest (grow bottom-up), then shorter struts, then downward-leaning ones.

This is a deterministic constructive ordering, not the full FrameFab
stiffness/collision optimisation — see Huang et al. 2016 (ACM TOG) and
Huang et al. 2018 'Choreo' for the research-grade planners to graduate to.
"""

from __future__ import annotations

import heapq

import numpy as np

from .graph import LatticeGraph


def order_struts(g: LatticeGraph, ground_tol: float = 5.0) -> list[dict]:
    """Return print steps: {strut, from, to, kind} in a buildable order."""
    z = g.nodes[:, 2]
    zmin = float(z.min()) if len(z) else 0.0
    grounded = set(np.where(z <= zmin + ground_tol)[0].tolist())

    adj: dict[int, list[int]] = {}
    for s, (i, j, _) in enumerate(g.struts):
        adj.setdefault(i, []).append(s)
        adj.setdefault(j, []).append(s)

    def priority(s: int) -> tuple:
        i, j, _ = g.struts[s]
        a, b = g.nodes[i], g.nodes[j]
        top = max(a[2], b[2])
        length = float(np.linalg.norm(a - b))
        return (round(top, 3), round(length, 3), s)

    printed_nodes = set(grounded)
    done = [False] * len(g.struts)
    ready: list[tuple] = []
    pushed = set()

    def push_candidates(node: int):
        for s in adj.get(node, []):
            if not done[s] and s not in pushed:
                heapq.heappush(ready, (*priority(s), s))
                pushed.add(s)

    for n in printed_nodes:
        push_candidates(n)

    steps: list[dict] = []
    while ready:
        *_, s = heapq.heappop(ready)
        if done[s]:
            continue
        i, j, kind = g.struts[s]
        ai, aj = i in printed_nodes, j in printed_nodes
        if not (ai or aj):
            pushed.discard(s)  # re-queue when an endpoint lands
            continue
        src, dst = (i, j) if ai else (j, i)
        done[s] = True
        steps.append({"strut": s, "from": int(src), "to": int(dst), "kind": kind})
        if dst not in printed_nodes:
            printed_nodes.add(dst)
            push_candidates(dst)
        push_candidates(src)

    unreachable = [s for s, d in enumerate(done) if not d]
    if unreachable:
        # disconnected islands (floating geometry) — report, print last from
        # nearest node once supports/fixtures exist
        steps.append({"unreachable": unreachable})
    return steps
