"""Exporters: waypoint CSV and Yaskawa INFORM .JBI jobs.

The JBI writer emits relative-job style Cartesian jobs (///POSTYPE ROBOT,
///RECTAN) in the active user frame. It is a *starting template*: validate in
MotoSim EG VRC against your cell, tool file and user frame before running on
the HC10DTP, and prefer MotoROS2 streaming for closed-loop production.

Per strut:  travel to approach point above the anchored node -> touch start ->
extruder ON (DOUT) -> linear extrude to free node -> dwell -> extruder OFF ->
detach retract along +Z. Extrusion rate can also be commanded via analog
output (AOUT) proportional to speed if the controller has the analog option.
"""

from __future__ import annotations

import csv
import datetime as _dt
import os

import numpy as np


def waypoints(g, steps, approach: float = 20.0, retract: float = 25.0):
    """Yield dict rows describing the motion program, units mm."""
    for step in steps:
        if "unreachable" in step:
            continue
        a = g.nodes[step["from"]]
        b = g.nodes[step["to"]]
        s = step["strut"]
        yield {"strut": s, "phase": "approach", "x": a[0], "y": a[1], "z": a[2] + approach, "extrude": 0}
        yield {"strut": s, "phase": "start",    "x": a[0], "y": a[1], "z": a[2],            "extrude": 0}
        yield {"strut": s, "phase": "extrude",  "x": b[0], "y": b[1], "z": b[2],            "extrude": 1}
        yield {"strut": s, "phase": "detach",   "x": b[0], "y": b[1], "z": b[2] + retract,  "extrude": 0}


def save_csv(path: str, g, steps, **kw) -> int:
    rows = list(waypoints(g, steps, **kw))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["strut", "phase", "x", "y", "z", "extrude"])
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})
    return len(rows)


def save_jbi(
    basepath: str,
    g,
    steps,
    job_name: str = "FORMGEN",
    tool: int = 0,
    user_frame: int = 1,
    speed_travel: float = 100.0,   # mm/s -> V= in 0.1mm/s units below
    speed_extrude: float = 25.0,
    dout_extrude: int = 1,
    struts_per_job: int = 250,
    approach: float = 20.0,
    retract: float = 25.0,
    rx: float = 180.0, ry: float = 0.0, rz: float = 0.0,
) -> list[str]:
    """Write one or more .JBI files; returns the file paths.

    Positions are emitted as Cartesian C-variables in the specified user frame
    with a fixed tool orientation (nozzle straight down by default). V= speeds
    are in mm/s (YRC1000micro INFORM uses 0.1 mm/s units internally — MotoSim
    will confirm scaling on import; adjust SPEED_SCALE if required).
    """
    SPEED_SCALE = 10.0  # INFORM V= is in 0.1 mm/s

    # Clear jobs from a previous run first. Job numbering restarts at 001 every
    # time, so a coarser re-run leaves higher-numbered files behind from the old
    # lattice -- and because the sequence looks continuous, an operator loading
    # 001..NNN would feed the controller two different structures.
    import glob as _glob
    stale = sorted(_glob.glob(basepath + "_[0-9][0-9][0-9].JBI"))
    for f in stale:
        try:
            os.remove(f)
        except OSError:
            pass

    good = [s for s in steps if "unreachable" not in s]
    chunks = [good[i:i + struts_per_job] for i in range(0, len(good), struts_per_job)]
    paths = []
    date = _dt.date.today().strftime("%Y/%m/%d")

    for ci, chunk in enumerate(chunks, start=1):
        name = f"{job_name}{ci:03d}"
        pts, inst = [], []

        def C(p) -> int:
            pts.append(p)
            return len(pts) - 1

        inst.append(f"DOUT OT#({dout_extrude}) OFF")
        inst.append("MOVJ VJ=10.00")
        for step in chunk:
            a = g.nodes[step["from"]]
            b = g.nodes[step["to"]]
            i0 = C((a[0], a[1], a[2] + approach))
            i1 = C((a[0], a[1], a[2]))
            i2 = C((b[0], b[1], b[2]))
            i3 = C((b[0], b[1], b[2] + retract))
            inst.append(f"MOVL C{i0:05d} V={speed_travel * SPEED_SCALE:.1f}")
            inst.append(f"MOVL C{i1:05d} V={speed_extrude * SPEED_SCALE:.1f}")
            inst.append(f"DOUT OT#({dout_extrude}) ON")
            inst.append(f"MOVL C{i2:05d} V={speed_extrude * SPEED_SCALE:.1f}")
            inst.append(f"DOUT OT#({dout_extrude}) OFF")
            inst.append("TIMER T=0.30")
            inst.append(f"MOVL C{i3:05d} V={speed_travel * SPEED_SCALE:.1f}")
        inst.append("MOVJ VJ=10.00")

        lines = [
            "/JOB",
            f"//NAME {name}",
            "//POS",
            f"///NPOS {len(pts)},0,0,0,0,0",
            f"///TOOL {tool}",
            "///POSTYPE USER",
            f"///USER {user_frame}",
            "///RECTAN",
            "///RCONF 1,0,0,0,0,0,0,0",
        ]
        for k, (x, y, z) in enumerate(pts):
            lines.append(
                f"C{k:05d}={x:.3f},{y:.3f},{z:.3f},{rx:.4f},{ry:.4f},{rz:.4f}"
            )
        lines += [
            "//INST",
            f"///DATE {date}",
            "///ATTR SC,RW",
            "///GROUP1 RB1",
            "NOP",
            *inst,
            "END",
        ]
        path = f"{basepath}_{ci:03d}.JBI"
        with open(path, "w", newline="\r\n") as f:
            f.write("\n".join(lines) + "\n")
        paths.append(path)
    return paths
