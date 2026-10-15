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
import io
import os



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


def waypoints_csv(g, steps, **kw) -> tuple[str, int]:
    """The waypoint table as CSV text (CRLF rows, as the csv module writes them) and its row count."""
    rows = list(waypoints(g, steps, **kw))
    f = io.StringIO(newline="")
    w = csv.DictWriter(f, fieldnames=["strut", "phase", "x", "y", "z", "extrude"])
    w.writeheader()
    for r in rows:
        w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})
    return f.getvalue(), len(rows)


def save_csv(path: str, g, steps, **kw) -> int:
    """Write the waypoint CSV (replacing an earlier file, never writing through a link)."""
    from aces import safeio

    text, n = waypoints_csv(g, steps, **kw)
    safeio.write_text(path, text, overwrite=True)
    return n


def save_jbi(basepath: str, g, steps, **kw) -> list[str]:
    """Write one or more .JBI files as ``<basepath>_NNN.JBI``; returns the file paths.

    Jobs from a previous run with the same base name are removed first: numbering restarts at 001 every time,
    so a coarser re-run would leave higher-numbered files behind from the old lattice, and an operator loading
    001..NNN would feed the controller two different structures.  Each job is written through
    :mod:`aces.safeio` (never through a link).  Keyword arguments are those of :func:`jbi_jobs`.
    """
    import glob as _glob

    from aces import safeio

    for f in sorted(_glob.glob(basepath + "_[0-9][0-9][0-9].JBI")):
        try:
            os.remove(f)  # removes a link itself, never its target
        except OSError:
            pass
    paths = []
    for ci, text in jbi_jobs(g, steps, **kw):
        path = f"{basepath}_{ci:03d}.JBI"
        safeio.write_text(path, text, overwrite=True)
        paths.append(path)
    return paths


def jbi_jobs(
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
) -> list[tuple[int, str]]:
    """The Yaskawa INFORM jobs as ``(job number, text)``; the text has CRLF line ends.

    Positions are emitted as Cartesian C-variables in the specified user frame
    with a fixed tool orientation (nozzle straight down by default). V= speeds
    are in mm/s (YRC1000micro INFORM uses 0.1 mm/s units internally — MotoSim
    will confirm scaling on import; adjust SPEED_SCALE if required).
    """
    SPEED_SCALE = 10.0  # INFORM V= is in 0.1 mm/s

    good = [s for s in steps if "unreachable" not in s]
    chunks = [good[i:i + struts_per_job] for i in range(0, len(good), struts_per_job)]
    jobs = []
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
        jobs.append((ci, "\r\n".join(lines) + "\r\n"))
    return jobs
