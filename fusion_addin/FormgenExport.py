"""Fusion 360 script: export selected bodies (or all visible) for formgen.

Install: Fusion 360 -> UTILITIES -> Scripts and Add-Ins (Shift+S) -> Scripts
-> "+" -> pick this folder. Run it, choose an output folder; it writes one
STEP (exact geometry, best for formgen) and one high-res STL per body.
T-spline Form bodies must be finished (solid/surface) before export.

Then:  python -m formgen.cli <body>.step --mode panel --strut 60 --thickness 80
"""

import adsk.core
import adsk.fusion
import os
import traceback


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            ui.messageBox("Open a Fusion design first.")
            return

        dlg = ui.createFolderDialog()
        dlg.title = "formgen: choose export folder"
        if dlg.showDialog() != adsk.core.DialogResults.DialogOK:
            return
        folder = dlg.folder

        # selected bodies, else all visible bodies in the root component
        bodies = []
        for i in range(ui.activeSelections.count):
            ent = ui.activeSelections.item(i).entity
            if isinstance(ent, adsk.fusion.BRepBody):
                bodies.append(ent)
        if not bodies:
            bodies = [b for b in design.rootComponent.bRepBodies if b.isVisible]
        if not bodies:
            ui.messageBox("No bodies to export.")
            return

        em = design.exportManager
        names = []
        for body in bodies:
            safe = "".join(c if c.isalnum() else "_" for c in body.name)
            step_path = os.path.join(folder, safe + ".step")
            step_opts = em.createSTEPExportOptions(step_path)
            # STEP export is per-design; isolate the body via a temp copy is
            # heavyweight — export whole design once, and per-body STL below.
            stl_path = os.path.join(folder, safe + ".stl")
            stl_opts = em.createSTLExportOptions(body, stl_path)
            stl_opts.meshRefinement = (
                adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
            )
            em.execute(stl_opts)
            names.append(safe)

        design_step = os.path.join(folder, "design_all_bodies.step")
        em.execute(em.createSTEPExportOptions(design_step))

        ui.messageBox(
            "Exported: design_all_bodies.step + per-body STL for\n- "
            + "\n- ".join(names)
            + f"\n\nNext:\npython -m formgen.cli {names[0]}.stl --mode panel"
        )
    except Exception:
        if ui:
            ui.messageBox("formgen export failed:\n{}".format(traceback.format_exc()))
