"""Flat PNG contact sheet of a lattice -- three orthographic views, PIL only.

No OpenGL, no offscreen context, nothing that can fail on a headless box: just
project the strut graph onto three planes and draw lines. Good enough to see at
a glance whether the tessellation did what you asked, and to paste into a quote.
"""

from __future__ import annotations

import numpy as np

_VIEWS = [("front  (XZ)", 0, 2, 1), ("top  (XY)", 0, 1, 2), ("side  (YZ)", 1, 2, 0)]

_KIND_COLOUR = {
    "outer": (127, 209, 185),
    "inner": (106, 176, 243),
    "tie":   (232, 190, 106),
    "web":   (150, 150, 165),
    "skin":  (127, 209, 185),
    "infill": (150, 150, 165),
}
_BG = (14, 17, 22)
_FG = (220, 227, 236)


def render_png(g, title: str = "", width: int = 1500, height: int = 520, margin: int = 34) -> bytes:
    """A three-view PNG of the lattice graph *g*, as bytes."""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), _BG)
    d = ImageDraw.Draw(img)

    def png() -> bytes:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    if len(g.nodes) == 0 or not g.struts:
        d.text((margin, margin), "empty lattice", fill=_FG)
        return png()

    nodes = np.asarray(g.nodes, dtype=float)
    cell_w = width // 3
    idx = np.array([(i, j) for i, j, _ in g.struts])
    kinds = [k for _, _, k in g.struts]

    for n, (label, ax, ay, _depth) in enumerate(_VIEWS):
        x0 = n * cell_w
        pts = nodes[:, [ax, ay]]
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        span = np.maximum(hi - lo, 1e-9)
        s = min((cell_w - 2 * margin) / span[0], (height - 2 * margin - 18) / span[1])
        off_x = x0 + (cell_w - span[0] * s) / 2.0
        off_y = margin + 12 + (height - 2 * margin - 12 - span[1] * s) / 2.0

        def to_px(p):
            u = off_x + (p[0] - lo[0]) * s
            v = height - (off_y + (p[1] - lo[1]) * s)   # flip: +Y up
            return (u, v)

        a = pts[idx[:, 0]]
        b = pts[idx[:, 1]]
        for k in range(len(idx)):
            d.line([to_px(a[k]), to_px(b[k])],
                   fill=_KIND_COLOUR.get(kinds[k], _FG), width=1)

        d.text((x0 + margin, 12), label, fill=(139, 152, 171))
        d.text((x0 + margin, height - 20),
               "{:.0f} x {:.0f} mm".format(span[0], span[1]), fill=(139, 152, 171))
        if n:
            d.line([(x0, 0), (x0, height)], fill=(42, 51, 66), width=1)

    if title:
        # top-left of the sheet, clear of the per-view labels along the bottom
        d.rectangle([0, 0, width, 8], fill=_BG)
        d.text((margin, height - 38), title, fill=_FG)
    return png()


def save_png(path: str, g, title: str = "", width: int = 1500,
             height: int = 520, margin: int = 34) -> str:
    """Write a three-view PNG of the lattice graph *g* (replacing an earlier file, never through a link)."""
    from aces import safeio

    safeio.write_bytes(path, render_png(g, title, width, height, margin), overwrite=True)
    return path
