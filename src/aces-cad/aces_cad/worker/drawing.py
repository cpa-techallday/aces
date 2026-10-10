"""Worker op `drawing`: a 1:1 mm orthographic drawing of a run's B-rep (spec section 14), in a job that runs no agent
code.

Views come from OCCT hidden-line removal (build123d project_to_viewport), de-duplicated, laid out in third- or
first-angle projection and written with build123d's ExportDXF on the VISIBLE and HIDDEN layers. ezdxf then adds the
dimension styles, the text style, the dashed hidden linetype, the DIMENSION entities (overall extents, hole callouts,
hole positions), the title block and the optional section outline. Every dimension carries the mm overrides below:
ezdxf's EZDXF style has dimlfac 100, which would print a 60 mm edge as 6000. The SVG and PNG previews are rendered
from that DXF by ezdxf, so they carry the dimensions. Dimension values are measured from the B-rep, never typed. Not a
GD&T drawing.
"""
from __future__ import annotations

import math

from .main import WorkerError

VIEW_DIRS = {"front": ((0, -1, 0), (0, 0, 1)), "back": ((0, 1, 0), (0, 0, 1)), "top": ((0, 0, 1), (0, 1, 0)),
             "bottom": ((0, 0, -1), (0, -1, 0)), "right": ((1, 0, 0), (0, 0, 1)), "left": ((-1, 0, 0), (0, 0, 1)),
             "iso": ((1, -1, 1), (0, 0, 1))}
ORTHO = ("front", "back", "top", "bottom", "right", "left")
PROJECTIONS = ("third-angle", "first-angle")
FORMATS = ("dxf", "svg", "png")
AXES = "xyz"
# the view that shows a hole as a circle: its axis model index -> preferred views
HOLE_VIEWS = {2: ("top", "bottom"), 1: ("front", "back"), 0: ("right", "left")}
MAX_DIMENSIONS = 200
MAX_POSITION_DIMS = 12
TABLE_CHUNK = 250       # hole-table rows per list in the result: spec 5.4 caps every list there at 500 items
MAX_TABLE_ROWS = 10_000  # rows returned for drawing.json (the result file is capped at 4 MiB); the DXF has them all
MAX_TABLE_COLUMNS = 6
CHAR_W = 0.85          # monospace advance per character, as a multiple of the text height (DejaVu Sans Mono)
HIDDEN_PATTERN = [4.5, 3.0, -1.5]
METHOD = ("OCCT hidden-line removal (build123d project_to_viewport), ezdxf DIMENSION entities on values measured "
          "from the B-rep")
SECTION_PLANES = {"x": ((1, 0, 0), (0, 1, 0), (1, 0, 0)), "y": ((0, 1, 0), (1, 0, 0), (0, -1, 0)),
                  "z": ((0, 0, 1), (1, 0, 0), (0, 0, 1))}


def fmt(value: float) -> str:
    """A length the way the dimensions print it (dimdec 2, trailing zeros suppressed)."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def view_frame(name: str):
    """(d, up, local x, local y): the projected frame of project_to_viewport (probed: front maps X, Z; right Y, Z;
    top X, Y; bottom X, -Y; back -X, Z; left -Y, Z)."""
    d, up = VIEW_DIRS[name]
    d = _unit(d)
    lx = _unit(_cross(up, d))
    k = _dot(up, d)
    ly = _unit(tuple(u - k * c for u, c in zip(up, d)))
    return d, up, lx, ly


class View:
    def __init__(self, name: str):
        self.name = name
        self.d, self.up, self.lx, self.ly = view_frame(name)
        self.visible: list = []
        self.hidden: list = []
        self.dups = 0
        self.coincident = 0
        self.ext = (0.0, 0.0, 0.0, 0.0)          # x0, x1, y0, y1 in the view's own frame (centre at the bbox centre)
        self.off = (0.0, 0.0)
        self.cell = (0, 0)

    def local(self, p, c) -> tuple:
        rel = tuple(a - b for a, b in zip(p, c))
        return (_dot(rel, self.lx), _dot(rel, self.ly))

    def at(self, p, c) -> tuple:
        x, y = self.local(p, c)
        return (x + self.off[0], y + self.off[1])

    def box(self) -> tuple:
        x0, x1, y0, y1 = self.ext
        return (x0 + self.off[0], x1 + self.off[0], y0 + self.off[1], y1 + self.off[1])

    @property
    def width(self) -> float:
        return self.ext[1] - self.ext[0]

    @property
    def height(self) -> float:
        return self.ext[3] - self.ext[2]


def _edge_key(edge) -> tuple:
    pts = []
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        p = edge.position_at(t)
        pts.append((round(p.X, 4) + 0.0, round(p.Y, 4) + 0.0, round(p.Z, 4) + 0.0))
    fwd, rev = tuple(pts), tuple(reversed(pts))
    return min(fwd, rev)


def dedupe(visible, hidden) -> tuple:
    """Drop duplicate visible edges, and hidden edges equal to a visible one (5 sampled points, 1e-4 mm, either
    direction). Returns (visible, hidden, duplicates_removed, hidden_coincident_removed)."""
    seen: set = set()
    vis = []
    dups = 0
    for e in visible:
        k = _edge_key(e)
        if k in seen:
            dups += 1
            continue
        seen.add(k)
        vis.append(e)
    hseen: set = set()
    hid = []
    coincident = 0
    for e in hidden:
        k = _edge_key(e)
        if k in seen:
            coincident += 1
            continue
        if k in hseen:
            dups += 1
            continue
        hseen.add(k)
        hid.append(e)
    return vis, hid, dups, coincident


def _corners(b):
    return [(x, y, z) for x in (b[0], b[3]) for y in (b[1], b[4]) for z in (b[2], b[5])]


def _edge_extents(edges) -> tuple | None:
    from build123d import Compound

    if not edges:
        return None
    bb = Compound(list(edges)).bounding_box(optimal=True)
    return (bb.min.X, bb.max.X, bb.min.Y, bb.max.Y)


def project_views(shape, names: list, b: list, c: tuple, diag: float, notes: list) -> dict:
    from build123d import Vector

    views = {}
    for name in names:
        v = View(name)
        origin = Vector(*c) + Vector(*v.d) * (10.0 * diag)
        vis, hid = shape.project_to_viewport(origin, viewport_up=v.up, look_at=Vector(*c))
        vis, hid, v.dups, v.coincident = dedupe(list(vis), list(hid))
        if name == "iso":
            hid = []
        v.visible, v.hidden = vis, hid
        if name in ORTHO:
            pts = [v.local(p, c) for p in _corners(b)]
            v.ext = (min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts))
        else:
            v.ext = _edge_extents(vis) or (0.0, 0.0, 0.0, 0.0)
        views[name] = v
    if "iso" in views:
        notes.append("the iso view is not dimensioned and shows visible edges only")
    return views


def layout(views: dict, projection: str, gap: float, b: list, c: tuple) -> None:
    """Offsets per view: front at the origin, projection alignment kept, iso top-right."""
    front = views.get("front") or View("front")
    if "front" not in views:
        pts = [front.local(p, c) for p in _corners(b)]
        front.ext = (min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts))
    fx0, fx1, fy0, fy1 = front.ext
    third = projection == "third-angle"
    front.off, front.cell = (0.0, 0.0), (0, 0)

    def above(v):
        v.off, v.cell = (0.0, fy1 + gap - v.ext[2]), (0, 1)

    def below(v):
        v.off, v.cell = (0.0, fy0 - gap - v.ext[3]), (0, -1)

    def right_of(v, x_edge, col):
        v.off, v.cell = (x_edge + gap - v.ext[0], 0.0), (col, 0)

    def left_of(v, x_edge, col):
        v.off, v.cell = (x_edge - gap - v.ext[1], 0.0), (col, 0)

    if "top" in views:
        (above if third else below)(views["top"])
    if "bottom" in views:
        (below if third else above)(views["bottom"])
    if "right" in views:
        if third:
            right_of(views["right"], fx1, 1)
        else:
            left_of(views["right"], fx0, -1)
    if "left" in views:
        if third:
            left_of(views["left"], fx0, -1)
        else:
            right_of(views["left"], fx1, 1)
    if "back" in views:
        row0 = [views[n] for n in ("front", "right", "left") if n in views and views[n].cell[1] == 0]
        row0.append(front)
        edge = max(v.off[0] + v.ext[1] for v in row0)
        col = max(v.cell[0] for v in row0) + 1
        right_of(views["back"], edge, col)
    if "iso" in views:
        iso = views["iso"]
        placed = [v for n, v in views.items() if n != "iso"] + [front]
        x_edge = max(v.off[0] + v.ext[1] for v in placed)
        iso.off = (x_edge + gap - iso.ext[0], fy1 + gap - iso.ext[2])
        iso.cell = (max(v.cell[0] for v in placed) + 1, 1)


def _free(view: View, side: str, occupied: set) -> bool:
    col, row = view.cell
    target = {"below": (col, row - 1), "above": (col, row + 1), "left": (col - 1, row), "right": (col + 1, row)}
    return target[side] not in occupied


def _callout_side(view: View, vside: str, occupied: set) -> str:
    """Hole callouts go to the side away from the vertical position dimensions, unless only the other side is free."""
    first, second = ("right", "left") if vside == "left" else ("left", "right")
    if not _free(view, first, occupied) and _free(view, second, occupied):
        return second
    return first


def _side(view: View, horizontal: bool, occupied: set) -> str:
    prefs = ("below", "above") if horizontal else ("left", "right")
    for s in prefs:
        if _free(view, s, occupied):
            return s
    return prefs[0]


class Dims:
    """DIMENSION entities with the mandatory mm overrides, and their drawing.json records."""

    def __init__(self, msp, h: float, notes: list):
        self.msp = msp
        self.h = h
        self.mm = {"dimlfac": 1.0, "dimtxt": h, "dimasz": 0.7 * h, "dimexe": 0.35 * h, "dimexo": 0.18 * h,
                   "dimgap": 0.18 * h, "dimdec": 2, "dimzin": 8, "dimtxsty": "ACES_TEXT"}
        self.records: list = []
        self.stack: dict = {}
        self.notes = notes
        self.full_noted = False

    def _room(self) -> bool:
        if len(self.records) < MAX_DIMENSIONS:
            return True
        if not self.full_noted:
            self.notes.append(f"dimensioning stopped at {MAX_DIMENSIONS} dimensions")
            self.full_noted = True
        return False

    def offset(self, view: View, side: str) -> float:
        k = self.stack.get((view.name, side), 0)
        self.stack[(view.name, side)] = k + 1
        return 3.0 * self.h + 2.5 * self.h * k

    def _record(self, dim, view: View, kind: str, value: float, feature: str) -> None:
        dim.render()
        ent = dim.dimension
        texts = []
        for e in ent.get_geometry_block() or []:
            if e.dxftype() == "MTEXT":
                texts.append(e.plain_text())
            elif e.dxftype() == "TEXT":
                texts.append(e.plain_text() if hasattr(e, "plain_text") else e.dxf.text)
        measured = float(ent.get_measurement())
        if abs(measured - value) > 1e-6:
            raise WorkerError("internal", f"dimension {feature} measures {measured} in the DXF, not the B-rep value "
                                          f"{value}")
        self.records.append({"id": f"d{len(self.records) + 1}", "view": view.name, "type": kind,
                             "value_mm": value, "text": " ".join(t.strip() for t in texts if t.strip()),
                             "feature": feature})

    def linear(self, view: View, p1, p2, side: str, feature: str) -> None:
        from ezdxf import ARROWS

        if not self._room():
            return
        x0, x1, y0, y1 = view.box()
        off = self.offset(view, side)
        horizontal = side in ("below", "above")
        if horizontal:
            base = (p1[0], y0 - off if side == "below" else y1 + off)
            value = abs(p2[0] - p1[0])
            angle = 0.0
        else:
            base = (x0 - off if side == "left" else x1 + off, p1[1])
            value = abs(p2[1] - p1[1])
            angle = 90.0
        if value <= 1e-9:
            return
        dim = self.msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, dimstyle="EZDXF",
                                      override=dict(self.mm, dimblk=ARROWS.closed_filled),
                                      dxfattribs={"layer": "DIMENSIONS"})
        self._record(dim, view, "linear", value, feature)

    def diameter(self, view: View, centre, radius: float, text: str, location, feature: str) -> None:
        if not self._room():
            return
        dim = self.msp.add_diameter_dim(center=centre, radius=radius, location=location, text=text,
                                        dimstyle="EZ_RADIUS", override=dict(self.mm, dimtoh=1),
                                        dxfattribs={"layer": "DIMENSIONS"})
        self._record(dim, view, "diameter", 2.0 * radius, feature)


def extent_dims(dims: Dims, views: dict, b: list, c: tuple, occupied: set, sketch: bool) -> None:
    """Overall sizes: width (X) from front, else top/bottom/back; height (Z) from front, else right/left/back; depth
    (Y) from right, else left/top/bottom. Values come from the B-rep bounding box."""
    lo, hi = (b[0], b[1], b[2]), (b[3], b[4], b[5])

    def place(view_names, axis: int, feature: str):
        for name in view_names:
            v = views.get(name)
            if v is None:
                continue
            e = [0.0, 0.0, 0.0]
            e[axis] = 1.0
            sx, sy = _dot(e, v.lx), _dot(e, v.ly)
            if abs(abs(sx) - 1.0) < 1e-9:
                horizontal = True
            elif abs(abs(sy) - 1.0) < 1e-9:
                horizontal = False
            else:
                continue
            side = _side(v, horizontal, occupied)
            x0, x1, y0, y1 = v.box()
            a = list(c)
            a[axis] = lo[axis]
            z = list(c)
            z[axis] = hi[axis]
            pa, pz = v.at(a, c), v.at(z, c)
            if horizontal:
                y = y0 if side == "below" else y1
                p1, p2 = (min(pa[0], pz[0]), y), (max(pa[0], pz[0]), y)
            else:
                x = x0 if side == "left" else x1
                p1, p2 = (x, min(pa[1], pz[1])), (x, max(pa[1], pz[1]))
            dims.linear(v, p1, p2, side, feature)
            return

    place(("front", "top", "bottom", "back"), 0, "extent_x")
    if not sketch:
        place(("front", "right", "left", "back"), 2, "extent_z")
    place(("right", "left", "top", "bottom"), 1, "extent_y")


def hole_text(group: list) -> str:
    """The callout after ezdxf's own leading diameter sign: <> THRU | <> DEEP t, with n X and the cbore/csk part."""
    h = group[0]
    text = "<>" + (" THRU" if h["through"] else f" DEEP {fmt(float(h['depth_mm']))}")
    if len(group) > 1:
        text = f"{len(group)}X " + text
    if h.get("cbore"):
        text += f", CBORE \u00d8{fmt(float(h['cbore']['diameter_mm']))} DEEP {fmt(float(h['cbore']['depth_mm']))}"
    elif h.get("csk"):
        text += f", CSK \u00d8{fmt(float(h['csk']['diameter_mm']))} X {fmt(float(h['csk']['angle_deg']))}\u00b0"
    return text


def _hole_ok(h) -> bool:
    try:
        return (float(h["diameter_mm"]) > 0 and len(h["axis_dir"]) == 3 and len(h["entry_point_mm"]) == 3
                and isinstance(h["through"], bool) and float(h["depth_mm"]) >= 0)
    except (KeyError, TypeError, ValueError):
        return False


def _group_key(h) -> tuple:
    def r(x):
        return round(float(x), 6)

    cb = h.get("cbore") or None
    cs = h.get("csk") or None
    return (h.get("kind"), r(h["diameter_mm"]), r(h["depth_mm"]), bool(h["through"]),
            (r(cb["diameter_mm"]), r(cb["depth_mm"])) if cb else None,
            (r(cs["diameter_mm"]), r(cs["angle_deg"])) if cs else None)


def hole_dims(dims: Dims, views: dict, holes: list, c: tuple, occupied: set, notes: list) -> list | None:
    """Diameter callouts per hole group and baseline position dimensions (or a hole table) in the axis view."""
    per_view: dict = {}
    for h in holes:
        ax = [float(x) for x in h["axis_dir"]]
        k = max(range(3), key=lambda i: abs(ax[i]))
        if abs(abs(ax[k]) - 1.0) > 1e-6:
            notes.append(f"hole {h['id']} has an axis that is not along X, Y or Z: it is not dimensioned")
            continue
        name = next((n for n in HOLE_VIEWS[k] if n in views), None)
        if name is None:
            notes.append(f"hole {h['id']} (axis {AXES[k].upper()}) is not dimensioned: add the "
                         f"{' or '.join(HOLE_VIEWS[k])} view")
            continue
        per_view.setdefault(name, []).append(h)
    table = []
    group_n = 0
    for name in [n for n in ORTHO if n in per_view]:
        v = views[name]
        groups: dict = {}
        for h in per_view[name]:
            groups.setdefault(_group_key(h), []).append(h)
        x0, x1, y0, y1 = v.box()
        hside = _side(v, True, occupied)
        vside = _side(v, False, occupied)
        side = _callout_side(v, vside, occupied)
        # callouts stand outside the view, stacked above its top edge; the hole nearest the callouts gets the lowest
        # slot, so the leaders of a row of holes do not cross
        sign = 1.0 if side == "right" else -1.0

        def anchor(group):
            """The member nearest the callouts (top right, or top left): the shortest leader."""
            return max(group, key=lambda r: sign * v.at(r["entry_point_mm"], c)[0] + v.at(r["entry_point_mm"], c)[1])

        ordered = sorted(groups.values(), key=lambda g: sign * v.at(anchor(g)["entry_point_mm"], c)[0])
        for k, group in enumerate(ordered):
            group_n += 1
            gid = f"g{group_n}"
            first = anchor(group)
            centre = v.at(first["entry_point_mm"], c)
            feature = gid if len(group) > 1 else first["id"]
            text = hole_text(group)
            y = y1 + dims.h + (len(ordered) - 1 - k) * 2.5 * dims.h
            if side == "right":
                location = (x1 + 2.0 * dims.h, y)
            else:
                location = (x0 - 2.0 * dims.h - CHAR_W * dims.h * (len(text) + 3), y)
            dims.diameter(v, centre, float(first["diameter_mm"]) / 2.0, text, location, feature)
        xs: dict = {}
        ys: dict = {}
        for h in per_view[name]:
            px, py = v.at(h["entry_point_mm"], c)
            xs.setdefault(round(px - x0, 6), (px, py, h))
            ys.setdefault(round(py - y0, 6), (px, py, h))
        lx = next(i for i in range(3) if abs(abs(v.lx[i]) - 1.0) < 1e-9)
        ly = next(i for i in range(3) if abs(abs(v.ly[i]) - 1.0) < 1e-9)
        if len(xs) + len(ys) <= MAX_POSITION_DIMS:
            for dx in sorted(xs):
                px, py, h = xs[dx]
                if dx > 1e-9:
                    yref = y0 if hside == "below" else y1
                    dims.linear(v, (x0, yref), (px, py), hside, f"pos_{h['id']}_{AXES[lx]}")
            for dy in sorted(ys):
                px, py, h = ys[dy]
                if dy > 1e-9:
                    xref = x0 if vside == "left" else x1
                    dims.linear(v, (xref, y0), (px, py), vside, f"pos_{h['id']}_{AXES[ly]}")
        else:
            notes.append(f"{name} view: {len(per_view[name])} holes need more than {MAX_POSITION_DIMS} position "
                         f"dimensions; their positions are in the hole table")
            for h in sorted(per_view[name], key=lambda r: (round(float(r["entry_point_mm"][ly]), 6),
                                                          round(float(r["entry_point_mm"][lx]), 6))):
                ep = [float(x) for x in h["entry_point_mm"]]
                call = hole_text([h]).replace("<>", "\u00d8" + fmt(float(h["diameter_mm"])))
                table.append({"id": h["id"].upper(), "view": name, "callout": call,
                              AXES[lx]: ep[lx], AXES[ly]: ep[ly],
                              "text": f"{h['id'].upper()} {call} {AXES[lx].upper()} {ep[lx]:.2f} "
                                      f"{AXES[ly].upper()} {ep[ly]:.2f}"})
    return table or None


def table_chunks(table: list | None, notes: list) -> list | None:
    """The hole table for the result file: at most MAX_TABLE_ROWS rows, in lists of at most TABLE_CHUNK rows (the
    parent joins them into drawing.json's hole_table)."""
    if not table:
        return None
    if len(table) > MAX_TABLE_ROWS:
        notes.append(f"the hole table has {len(table)} rows: drawing.json lists the first {MAX_TABLE_ROWS}, the DXF "
                     f"lists them all")
        table = table[:MAX_TABLE_ROWS]
    return [table[i:i + TABLE_CHUNK] for i in range(0, len(table), TABLE_CHUNK)]


def place_hole_table(msp, table: list, gap: float, h: float) -> None:
    """The hole table as TEXT on TITLE, right of everything drawn so far, in columns no taller than the drawing; a
    table that would need more than MAX_TABLE_COLUMNS such columns gets taller columns instead of a wider sheet."""
    from ezdxf.enums import TextEntityAlignment

    x0, x1, y0, y1 = _extents(msp)
    pitch = 1.6 * h
    per_column = max(8, int((y1 - y0) / pitch) - 1, math.ceil(len(table) / MAX_TABLE_COLUMNS))
    width = (max(len(r["text"]) for r in table) + 3) * CHAR_W * h
    views = sorted({r["view"] for r in table})
    header = (f"HOLE TABLE ({', '.join(v.upper() for v in views)} VIEW): POSITIONS IN mm FROM THE MODEL ORIGIN")
    left = x1 + gap
    _add_text(msp, header, (left, y1), h, TextEntityAlignment.TOP_LEFT)
    for k, row in enumerate(table):
        col, line = divmod(k, per_column)
        _add_text(msp, row["text"], (left + col * width, y1 - (line + 1.5) * pitch), h, TextEntityAlignment.TOP_LEFT)


def _add_text(msp, text: str, at, h: float, align) -> None:
    t = msp.add_text(text, height=h, dxfattribs={"style": "ACES_TEXT", "layer": "TITLE"})
    t.set_placement(at, align=align)


def _extents(msp):
    from ezdxf import bbox

    box = bbox.extents(msp)
    if not box.has_data:
        return (0.0, 0.0, 0.0, 0.0)
    return (box.extmin.x, box.extmax.x, box.extmin.y, box.extmax.y)


def section_outline(shape, sect: dict, gap: float, h: float, msp, notes: list, warnings: list) -> dict | None:
    """The section-plane outline flattened onto the SECTION layer below the views (no hatching in 0.1.0)."""
    from build123d import Plane, section
    from ezdxf.enums import TextEntityAlignment

    axis = sect.get("axis")
    at = float(sect.get("at_mm"))
    if axis not in SECTION_PLANES:
        raise WorkerError("usage", "section axis must be x, y or z")
    o, xd, zd = SECTION_PLANES[axis]
    plane = Plane(origin=tuple(at * k for k in o), x_dir=xd, z_dir=zd)
    try:
        sk = section(shape, section_by=plane)
        faces = sk.faces()
    except Exception as e:  # noqa: BLE001 (a kernel failure here is reported, not fatal)
        warnings.append(f"section {axis.upper()} = {fmt(at)}: {type(e).__name__}: {str(e)[:200]}")
        return None
    if not faces or sum(f.area for f in faces) <= 0:
        warnings.append(f"section {axis.upper()} = {fmt(at)}: the plane misses the part")
        return None
    local = plane.to_local_coords(sk)
    edges = local.edges()
    bb = local.bounding_box()
    x0, x1, y0, y1 = _extents(msp)
    dx = x0 - bb.min.X
    dy = (y0 - gap) - bb.max.Y
    pts_layer = {"layer": "SECTION"}
    for e in edges:
        moved = e.translate((dx, dy, 0.0))
        _add_edge(msp, moved, pts_layer)
    label = f"SECTION {axis.upper()} = {at:.2f}"
    t = msp.add_text(label, height=h, dxfattribs={"style": "ACES_TEXT", "layer": "SECTION"})
    t.set_placement((x0, dy + bb.min.Y - 1.8 * h), align=TextEntityAlignment.BOTTOM_LEFT)
    notes.append("section outline only: no hatching in 0.1.0")
    return {"axis": axis, "at_mm": at, "label": label, "area_mm2": float(sum(f.area for f in faces)),
            "offset_mm": [dx, dy]}


def _add_edge(msp, edge, attribs: dict) -> None:
    """One flattened section edge as LINE, ARC/CIRCLE or a polyline approximation (z = 0 plane)."""
    gt = str(edge.geom_type).split(".")[-1]
    if gt == "LINE":
        a, b = edge.position_at(0), edge.position_at(1)
        msp.add_line((a.X, a.Y), (b.X, b.Y), dxfattribs=attribs)
        return
    pts = [edge.position_at(i / 64.0) for i in range(65)]
    msp.add_lwpolyline([(p.X, p.Y) for p in pts], dxfattribs=attribs)


def _style_dxf(doc, h: float) -> None:
    from ezdxf.tools.standards import setup_dimstyles

    setup_dimstyles(doc)
    if "ACES_TEXT" not in doc.styles:
        doc.styles.add("ACES_TEXT", font="DejaVuSansMono.ttf")
    if "ACES_HIDDEN" not in doc.linetypes:
        doc.linetypes.add("ACES_HIDDEN", pattern=list(HIDDEN_PATTERN), description="hidden, 3 mm dash, 1.5 mm gap")
    for name, colour, weight, ltype in (("VISIBLE", 7, 35, "Continuous"), ("HIDDEN", 8, 18, "ACES_HIDDEN"),
                                        ("DIMENSIONS", 7, None, "Continuous"), ("TITLE", 7, None, "Continuous"),
                                        ("SECTION", 7, 35, "Continuous")):
        layer = doc.layers.get(name) if name in doc.layers else doc.layers.add(name)
        layer.color = colour
        if weight is not None:
            layer.dxf.lineweight = weight
        layer.dxf.linetype = ltype
    for e in doc.modelspace():
        if e.dxf.get("layer") in ("VISIBLE", "HIDDEN"):
            e.dxf.linetype = "BYLAYER"
            e.dxf.color = 256
            e.dxf.lineweight = -1
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    doc.header["$LTSCALE"] = 1.0
    _ = h


def previews(doc, svg_path, png_path, size_in: tuple) -> None:
    """SVG and PNG rendered from the DXF by ezdxf (DejaVu Sans Mono from matplotlib for the diameter sign)."""
    from pathlib import Path

    import matplotlib

    matplotlib.use("Agg")
    from ezdxf.addons.drawing import Frontend, RenderContext, layout, svg
    from ezdxf.addons.drawing import matplotlib as dxf_mpl
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration, LinePolicy
    from ezdxf.fonts import fonts

    fonts.font_manager.clear()
    fonts.font_manager.build([str(Path(matplotlib.get_data_path()) / "fonts" / "ttf")], support_dirs=False)
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.COLOR,
                        line_policy=LinePolicy.ACCURATE)
    msp = doc.modelspace()
    if svg_path is not None:
        backend = svg.SVGBackend()
        Frontend(RenderContext(doc), backend, config=cfg).draw_layout(msp)
        text = backend.get_string(layout.Page(0, 0, layout.Units.mm, margins=layout.Margins.all(10)))
        Path(svg_path).write_text(text, encoding="utf-8")
    if png_path is not None:
        dxf_mpl.qsave(msp, str(png_path), bg="#FFFFFF", dpi=150, config=cfg, size_inches=size_in)


def _args(args: dict) -> dict:
    views = args.get("views")
    if not isinstance(views, list) or not views or len(set(views)) != len(views) or \
            any(v not in VIEW_DIRS for v in views):
        raise WorkerError("usage", f"views must be distinct values from {', '.join(VIEW_DIRS)}")
    projection = args.get("projection")
    if projection not in PROJECTIONS:
        raise WorkerError("usage", f"projection must be {' or '.join(PROJECTIONS)}")
    formats = args.get("formats")
    if not isinstance(formats, list) or not formats or any(f not in FORMATS for f in formats):
        raise WorkerError("usage", f"formats must be values from {', '.join(FORMATS)}")
    stem = args.get("stem")
    if not isinstance(stem, str) or not stem or "/" in stem or "\\" in stem or stem.startswith("."):
        raise WorkerError("internal", "the drawing job has no valid file stem")
    return {"views": views, "projection": projection, "formats": formats, "stem": stem,
            "dimensions": args.get("dimensions") is not False, "section": args.get("section"),
            "title_block": args.get("title_block") or {}}


def _title_lines(tb: dict, projection: str, b123d: str) -> list:
    def clean(v, limit=80):
        return "".join(ch for ch in str(v if v is not None else "-") if ch.isprintable())[:limit]

    proj = "THIRD ANGLE PROJECTION" if projection == "third-angle" else "FIRST ANGLE PROJECTION"
    return [clean(tb.get("title")),
            f"PROJECT {clean(tb.get('project'))} ({clean(tb.get('class'))})",
            f"RUN {clean(tb.get('run_id'))}",
            f"SCRIPT {clean(tb.get('script_sha256'))[:8]}  PARAMS {clean(tb.get('params_sha256'))[:8]}",
            f"SCALE 1:1  UNITS mm  {proj}",
            f"DATE {clean(tb.get('date'))} (UTC)",
            f"aces-cad 0.1.0 / build123d {clean(b123d)} - not a GD&T drawing: check before manufacture"]


def op_drawing(args: dict, ctx) -> dict:
    import build123d
    import ezdxf
    from build123d import Compound, ExportDXF, Pos, Unit
    from ezdxf.enums import TextEntityAlignment

    from . import geom

    a = _args(args)
    if ctx.run_dir is None:
        raise WorkerError("internal", "the drawing job has no run directory")
    loaded = geom.load(ctx.run_dir / "out")
    if loaded.kind == "mesh":
        raise WorkerError("usage", "drawings need a B-rep: this run's result is a mesh")
    parts = geom.select(loaded, args.get("part"))
    if any(p.shape is None for p in parts):
        raise WorkerError("usage", "drawings need a B-rep: the selected part is a mesh")
    shape = parts[0].shape if len(parts) == 1 else Compound([p.shape for p in parts])
    sketch = all(p.kind == "sketch" for p in parts)
    notes: list = []
    warnings: list = []
    names = list(a["views"])
    if sketch:
        if names != ["top"]:
            notes.append("a sketch is drawn in its top view only, with its extent dimensions")
        names = ["top"]
    b = [float(x) for x in geom.bbox(shape)]
    c = ((b[0] + b[3]) / 2.0, (b[1] + b[4]) / 2.0, (b[2] + b[5]) / 2.0)
    diag = math.dist(b[:3], b[3:])
    if diag <= 0:
        raise WorkerError("result", "the part has an empty bounding box")
    if sketch and (b[5] - b[2]) > 1e-6 * diag:
        warnings.append("the sketch is not in an XY plane: its top view shows it at an angle or edge-on")
    views = project_views(shape, names, b, c, diag, notes)
    largest = max(max(v.width, v.height) for v in views.values())
    h = min(10.0, max(2.5, largest / 20.0))
    gap = max(20.0, 0.25 * largest)
    layout(views, a["projection"], gap, b, c)
    occupied = {v.cell for v in views.values()}

    exporter = ExportDXF(unit=Unit.MM)
    exporter.add_layer("VISIBLE")
    exporter.add_layer("HIDDEN")
    # hidden edges first, so the visible ones draw over them (an edge-on hole circle lies on a visible edge)
    for layer, attr in (("HIDDEN", "hidden"), ("VISIBLE", "visible")):
        for v in views.values():
            moved = [Pos(v.off[0], v.off[1], 0) * e for e in getattr(v, attr)]
            if moved:
                exporter.add_shape(moved, layer=layer)
    dxf_path = ctx.out_file(f"{a['stem']}.dxf")
    exporter.write(str(dxf_path))

    doc = ezdxf.readfile(str(dxf_path))
    _style_dxf(doc, h)
    msp = doc.modelspace()
    dims = Dims(msp, h, notes)
    hole_table = None
    holes = []
    if a["dimensions"]:
        extent_dims(dims, views, b, c, occupied, sketch)
        if not sketch:
            for k, p in enumerate(parts):
                try:
                    found = geom.find_holes(p.shape) if p.kind != "sketch" else []
                except Exception as e:  # noqa: BLE001 (hole detection is best effort for a drawing)
                    warnings.append(f"hole detection failed on {p.name}: {type(e).__name__}: {str(e)[:200]}")
                    found = []
                for hole in found:
                    if not _hole_ok(hole):
                        warnings.append("a hole record without the expected fields was skipped")
                        continue
                    holes.append(dict(hole, part=p.name, _order=k))
            # numbered in position order (part, then z, y, x of the entry point), so callouts and tables read in rows
            holes.sort(key=lambda r: (r["_order"],) + tuple(round(float(r["entry_point_mm"][i]), 6) for i in (2, 1, 0)))
            for n, hole in enumerate(holes, 1):
                hole["id"] = f"h{n}"
                hole.pop("_order", None)
            hole_table = hole_dims(dims, views, holes, c, occupied, notes)
    section = None
    if a["section"]:
        section = section_outline(shape, a["section"], gap, h, msp, notes, warnings)
    if hole_table:
        place_hole_table(msp, hole_table, gap, h)
    x0, x1, y0, y1 = _extents(msp)
    lines = _title_lines(a["title_block"], a["projection"], build123d.__version__)
    top = y0 - 2.0 * h
    for i, line in enumerate(lines):
        _add_text(msp, line, (x1, top - i * 1.6 * h), h, TextEntityAlignment.TOP_RIGHT)
    auditor = doc.audit()
    if len(auditor.errors):
        msgs = "; ".join(str(getattr(e, "message", e)) for e in list(auditor.errors)[:10])
        raise WorkerError("internal", f"the DXF audit found {len(auditor.errors)} errors: {msgs}")
    doc.saveas(str(dxf_path))

    files = [f"{a['stem']}.dxf"]
    ex = _extents(msp)
    w_mm, h_mm = max(1.0, ex[1] - ex[0]) + 20.0, max(1.0, ex[3] - ex[2]) + 20.0
    scale = 11.69 / max(w_mm, h_mm)
    size_in = (max(2.0, w_mm * scale), max(2.0, h_mm * scale))
    svg_path = ctx.out_file(f"{a['stem']}.svg") if "svg" in a["formats"] else None
    png_path = ctx.out_file(f"{a['stem']}.png") if "png" in a["formats"] else None
    previews(doc, svg_path, png_path, size_in)
    files += [p.name for p in (svg_path, png_path) if p is not None]

    out_views = []
    for name in names:
        v = views[name]
        out_views.append({"name": name, "direction": list(VIEW_DIRS[name][0]), "up": list(VIEW_DIRS[name][1]),
                          "offset_mm": [v.off[0], v.off[1]], "extent_mm": [v.width, v.height],
                          "visible_edges": len(v.visible), "hidden_edges": len(v.hidden),
                          "duplicates_removed": v.dups, "hidden_coincident_removed": v.coincident})
    chunks = table_chunks(hole_table, notes)
    return {"part": args.get("part"), "projection": a["projection"], "views": out_views,
            "dimensions": dims.records, "hole_table_chunks": chunks, "audit_errors": len(auditor.errors),
            "section": section, "text_height_mm": h, "gap_mm": gap, "files": files,
            "notes": list(dict.fromkeys(notes)), "warnings": list(dict.fromkeys(warnings))}
