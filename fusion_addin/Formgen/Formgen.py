"""formgen add-in for Autodesk Fusion 360.

Adds a "formgen lattice" button to the Solid tab's Create panel. Select a body,
press the button, set the lattice and the scale model, hit OK: the add-in
exports the body to STL, hands it to the formgen pipeline (running in formgen's
own Python venv, since Fusion's interpreter has no trimesh), and opens the
output folder when it finishes.

Install
-------
Fusion 360 -> UTILITIES -> Scripts and Add-Ins (Shift+S) -> Add-Ins tab
-> the green "+" -> pick this *Formgen* folder -> Run (tick "Run on Startup").

The add-in finds formgen in this order:
    1. the FORMGEN_HOME environment variable
    2. two levels up from this file (works when run straight from the repo)
    3. %USERPROFILE%\\formgen
    4. the path saved in formgen_path.txt beside this file
"""

import json
import os
import subprocess
import sys
import traceback

import adsk.core
import adsk.fusion

CMD_ID = "formgenLatticeCmd"
CMD_NAME = "formgen lattice"
CMD_TOOLTIP = ("Tessellate the selected body into a printable lattice: robot job, "
               "3D viewer, and a desktop-printable scale model.")
PANEL_ID = "SolidCreatePanel"

_handlers = []          # Fusion garbage-collects handlers unless we keep them
_app = None
_ui = None

PRINTERS = ["x1c", "p1s", "a1", "a1mini", "h2d", "mk4", "xl", "ender3", "elegoo",
            "sla", "desk"]


# ---------------------------------------------------------------- locating
def _candidate_roots():
    here = os.path.dirname(os.path.realpath(__file__))
    yield os.environ.get("FORMGEN_HOME", "")
    yield os.path.abspath(os.path.join(here, "..", ".."))   # the ACES checkout this add-in ships in
    yield os.path.join(os.path.expanduser("~"), "aces")
    yield os.path.join(os.path.expanduser("~"), "formgen")  # the original standalone folder
    saved = os.path.join(here, "formgen_path.txt")
    if os.path.exists(saved):
        try:
            with open(saved) as f:
                yield f.read().strip()
        except Exception:
            pass


def _package_dir(root):
    """Folder that contains the formgen package: the root itself, or root/src (ACES layout)."""
    for d in (root, os.path.join(root, "src")):
        if os.path.isdir(os.path.join(d, "formgen")):
            return d
    return None


def find_formgen():
    """Return (package_dir, python_exe) or raise.  formgen runs with cwd=package_dir."""
    for root in _candidate_roots():
        if not root:
            continue
        pkg = _package_dir(root)
        if pkg is None:
            continue
        for exe in (os.path.join(root, ".venv", "Scripts", "python.exe"),
                    os.path.join(root, ".venv", "bin", "python"),
                    sys.executable):
            if exe and os.path.exists(exe):
                return pkg, exe
    raise RuntimeError(
        "cannot find the formgen project. Set a FORMGEN_HOME environment "
        "variable, or drop a formgen_path.txt next to this add-in containing "
        "the full path to the formgen folder.")


# ------------------------------------------------------------------ export
def export_selected_body(folder):
    """Write the selected (or first visible) body to STL. Returns (path, name)."""
    design = adsk.fusion.Design.cast(_app.activeProduct)
    if not design:
        raise RuntimeError("open a Fusion design first")

    body = None
    for i in range(_ui.activeSelections.count):
        ent = _ui.activeSelections.item(i).entity
        if isinstance(ent, adsk.fusion.BRepBody):
            body = ent
            break
    if body is None:
        root = design.rootComponent
        candidates = [b for b in root.bRepBodies if b.isVisible]
        if not candidates:
            for occ in root.allOccurrences:
                candidates += [b for b in occ.bRepBodies
                               if b.isVisible and occ.isLightBulbOn]
        if not candidates:
            raise RuntimeError("select a body first (nothing visible to export)")
        body = candidates[0]

    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in body.name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, safe + ".stl")
    em = design.exportManager
    opts = em.createSTLExportOptions(body, path)
    opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
    em.execute(opts)
    return path, safe


# ------------------------------------------------------------------ handlers
class ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            inputs = args.command.commandInputs
            g = lambda i: inputs.itemById(i)          # noqa: E731
            out_dir = g("out").text.strip() or os.path.join(
                os.path.expanduser("~"), "formgen-out")

            root, python_exe = find_formgen()
            stl_path, name = export_selected_body(os.path.join(out_dir, "_fusion_export"))

            cmd = [python_exe, "-m", "formgen.cli", stl_path,
                   "--out", out_dir,
                   "--mode", g("mode").selectedItem.name,
                   "--strut", str(g("strut").value * 10.0),        # cm -> mm
                   "--thickness", str(g("thickness").value * 10.0),
                   "--strut-dia", str(g("dia").value * 10.0)]
            if g("jbi").value:
                cmd.append("--jbi")
            if g("model").value:
                cmd += ["--model", "--printer", g("printer").selectedItem.name]
                ratio = g("ratio").value
                if ratio and ratio > 0:
                    cmd += ["--ratio", str(ratio)]
                base = g("base").value * 10.0
                if base > 0:
                    cmd += ["--base", str(base)]

            flags = 0
            if sys.platform.startswith("win"):
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                                  creationflags=flags)
            log = (proc.stdout or "") + (proc.stderr or "")

            if proc.returncode != 0:
                _ui.messageBox("formgen failed:\n\n" + log[-2500:], CMD_NAME)
                return

            report = os.path.join(out_dir, name + "_estimate.json")
            summary = log.strip().split("\n")[-8:]
            if os.path.exists(report):
                try:
                    with open(report) as f:
                        rep = json.load(f)
                    st = rep["stats"]
                    est = rep["estimate"]
                    lines = ["{} nodes / {} struts, {:.1f} m of bead".format(
                        st["nodes"], st["struts"], st["total_length_m"]),
                        "full size: {} kg, {} h".format(
                            est["material_mass_kg"], est["total_time_h"])]
                    plan = rep.get("scale_model")
                    if plan:
                        lines.append("scale model {} -> {} mm on {}".format(
                            plan["ratio_label"],
                            " x ".join(str(v) for v in plan["model_size_mm"]),
                            plan["printer"]))
                        lines.append("model strut {} mm ({}x exaggerated)".format(
                            plan["model_strut_mm"], plan["strut_exaggeration"]))
                        for w in plan.get("warnings", []):
                            lines.append("! " + w)
                    summary = lines
                except Exception:
                    pass

            _ui.messageBox("formgen done.\n\n" + "\n".join(summary) +
                           "\n\nOutputs in:\n" + out_dir, CMD_NAME)
            try:
                os.startfile(out_dir)  # noqa: S606
            except Exception:
                pass
        except Exception:
            if _ui:
                _ui.messageBox("formgen add-in error:\n{}".format(
                    traceback.format_exc()), CMD_NAME)


class CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            cmd = args.command
            cmd.setDialogInitialSize(430, 520)
            inputs = cmd.commandInputs
            units = "cm"   # ValueInput is always in internal cm; we convert to mm

            inputs.addTextBoxCommandInput(
                "hint", "", "Select a body, then set the lattice. Values in mm.",
                2, True)

            mode = inputs.addDropDownCommandInput(
                "mode", "mode", adsk.core.DropDownStyles.TextListDropDownStyle)
            for m in ("panel", "skin", "infill"):
                mode.listItems.add(m, m == "panel")

            inputs.addValueInput("strut", "strut length (mm)", units,
                                 adsk.core.ValueInput.createByReal(6.0))
            inputs.addValueInput("thickness", "panel depth (mm)", units,
                                 adsk.core.ValueInput.createByReal(8.0))
            inputs.addValueInput("dia", "bead diameter (mm)", units,
                                 adsk.core.ValueInput.createByReal(0.8))

            inputs.addBoolValueInput("model", "printable scale model", True, "", True)
            printer = inputs.addDropDownCommandInput(
                "printer", "printer", adsk.core.DropDownStyles.TextListDropDownStyle)
            for p in PRINTERS:
                printer.listItems.add(p, p == "x1c")
            inputs.addFloatSpinnerCommandInput(
                "ratio", "ratio 1:N (0 = auto-fit)", "", 0.0, 5000.0, 1.0, 0.0)
            inputs.addValueInput("base", "base plate (mm)", units,
                                 adsk.core.ValueInput.createByReal(0.0))

            inputs.addBoolValueInput("jbi", "write Yaskawa .JBI jobs", True, "", False)
            inputs.addStringValueInput(
                "out", "output folder",
                os.path.join(os.path.expanduser("~"), "formgen-out"))

            on_exec = ExecuteHandler()
            cmd.execute.add(on_exec)
            _handlers.append(on_exec)
        except Exception:
            if _ui:
                _ui.messageBox("formgen add-in error:\n{}".format(
                    traceback.format_exc()), CMD_NAME)


# ---------------------------------------------------------------- lifecycle
def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        defs = _ui.commandDefinitions
        existing = defs.itemById(CMD_ID)
        if existing:
            existing.deleteMe()
        cmd_def = defs.addButtonDefinition(CMD_ID, CMD_NAME, CMD_TOOLTIP)

        on_created = CreatedHandler()
        cmd_def.commandCreated.add(on_created)
        _handlers.append(on_created)

        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        if panel and not panel.controls.itemById(CMD_ID):
            panel.controls.addCommand(cmd_def)
    except Exception:
        if _ui:
            _ui.messageBox("formgen add-in failed to start:\n{}".format(
                traceback.format_exc()))


def stop(context):
    try:
        panel = _ui.allToolbarPanels.itemById(PANEL_ID) if _ui else None
        if panel:
            ctrl = panel.controls.itemById(CMD_ID)
            if ctrl:
                ctrl.deleteMe()
        if _ui:
            cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
            if cmd_def:
                cmd_def.deleteMe()
    except Exception:
        if _ui:
            _ui.messageBox("formgen add-in failed to stop:\n{}".format(
                traceback.format_exc()))
