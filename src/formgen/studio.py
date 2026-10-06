"""formgen Studio -- the desktop front-end.

    python -m formgen.studio          (or double-click formgen-studio.cmd)

Pick a file or pull the live body straight out of Fusion 360, set the lattice,
tick "printable scale model", press Run. Everything happens on a worker thread
so the window keeps painting; the log is the same text the CLI prints.

Tkinter only -- no extra dependencies beyond what the pipeline already needs.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import scale
from .pipeline import Options, run

APP_TITLE = "formgen Studio"
SELECTION_LABEL = "<current Fusion selection>"


def _enable_hidpi() -> float:
    """Windows: opt into per-monitor DPI. Returns the display scale factor.

    Being DPI-aware makes the text crisp but also means Tk sizes everything in
    physical pixels, so the caller has to multiply its own geometry by the
    factor returned here or the window comes out tiny on a 150% display.
    """
    if not sys.platform.startswith("win"):
        return 1.0
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return 1.0
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
    except Exception:
        return 1.0


def _open_path(path: str) -> None:
    """Open a file or folder in the OS shell."""
    if not path or not os.path.exists(path):
        return
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class Studio(tk.Tk):
    def __init__(self, ui_scale: float = 1.0):
        super().__init__()
        self.ui_scale = max(1.0, float(ui_scale))
        self.title(APP_TITLE)
        k = self.ui_scale
        self.geometry("{}x{}".format(int(1000 * k), int(760 * k)))
        self.minsize(int(880 * k), int(620 * k))
        if k > 1.01:
            # scale fonts/widget metrics to match the window we just asked for
            self.tk.call("tk", "scaling", k * 96.0 / 72.0)

        self.log_q: "queue.Queue[str]" = queue.Queue()
        self.result: dict | None = None
        self.worker: threading.Thread | None = None
        self.fusion_bodies: list = []

        self._build_vars()
        self._build_ui()
        self.after(120, self._drain_log)
        self.after(300, self.refresh_fusion)

    # ------------------------------------------------------------------ vars
    def _build_vars(self):
        v = tk.StringVar
        self.var_source = v(value="file")
        self.var_input = v()
        self.var_body = v(value=SELECTION_LABEL)
        self.var_out = v(value=os.path.join(os.path.expanduser("~"), "formgen-out"))
        self.var_mode = v(value="panel")
        self.var_strut = v(value="60")
        self.var_thickness = v(value="80")
        self.var_dia = v(value="8")
        self.var_density = v(value="1.10")
        self.var_speed = v(value="25")

        self.var_model = tk.BooleanVar(value=True)
        self.var_printer = v(value=scale.DEFAULT_PRINTER)
        self.var_ratio = v(value="auto")
        self.var_minstrut = v(value="")
        self.var_base = v(value="0")
        self.var_model_union = tk.BooleanVar(value=False)
        self.var_enlarge = tk.BooleanVar(value=False)

        self.var_preview = tk.BooleanVar(value=True)
        self.var_viewer = tk.BooleanVar(value=True)
        self.var_waypoints = tk.BooleanVar(value=True)
        self.var_jbi = tk.BooleanVar(value=False)
        self.var_union = tk.BooleanVar(value=False)

        self.var_status = v(value="ready")
        self.var_fusion_status = v(value="checking Fusion...")

    # -------------------------------------------------------------------- ui
    def _build_ui(self):
        pad = {"padx": 6, "pady": 3}
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # ---- source ----------------------------------------------------
        src = ttk.LabelFrame(root, text="1 - geometry", padding=8)
        src.pack(fill="x")
        ttk.Radiobutton(src, text="File", value="file", variable=self.var_source,
                        command=self._sync_source).grid(row=0, column=0, sticky="w", **pad)
        e = ttk.Entry(src, textvariable=self.var_input, width=74)
        e.grid(row=0, column=1, columnspan=3, sticky="we", **pad)
        ttk.Button(src, text="Browse...", command=self.browse_input).grid(row=0, column=4, **pad)

        ttk.Radiobutton(src, text="Live Fusion 360", value="fusion",
                        variable=self.var_source,
                        command=self._sync_source).grid(row=1, column=0, sticky="w", **pad)
        self.cb_body = ttk.Combobox(src, textvariable=self.var_body, width=71, state="disabled")
        self.cb_body.grid(row=1, column=1, columnspan=3, sticky="we", **pad)
        ttk.Button(src, text="Refresh", command=self.refresh_fusion).grid(row=1, column=4, **pad)
        ttk.Label(src, textvariable=self.var_fusion_status, foreground="#666").grid(
            row=2, column=1, columnspan=4, sticky="w", padx=6)
        src.columnconfigure(1, weight=1)

        # ---- two columns -----------------------------------------------
        cols = ttk.Frame(root)
        cols.pack(fill="x", pady=(8, 0))

        lat = ttk.LabelFrame(cols, text="2 - lattice", padding=8)
        lat.pack(side="left", fill="both", expand=True)
        r = 0
        ttk.Label(lat, text="mode").grid(row=r, column=0, sticky="w", **pad)
        ttk.Combobox(lat, textvariable=self.var_mode, width=12, state="readonly",
                     values=["panel", "skin", "infill"]).grid(row=r, column=1, sticky="w", **pad)
        r += 1
        for label, var, tip in [
            ("strut length (mm)", self.var_strut, "target triangle edge"),
            ("panel depth (mm)", self.var_thickness, "panel/infill cell"),
            ("bead diameter (mm)", self.var_dia, "real printed strut"),
            ("material g/cm3", self.var_density, "ABS-CF ~ 1.10"),
            ("extrude speed mm/s", self.var_speed, ""),
        ]:
            ttk.Label(lat, text=label).grid(row=r, column=0, sticky="w", **pad)
            ttk.Entry(lat, textvariable=var, width=10).grid(row=r, column=1, sticky="w", **pad)
            if tip:
                ttk.Label(lat, text=tip, foreground="#888").grid(row=r, column=2, sticky="w")
            r += 1
        ttk.Checkbutton(lat, text="boolean-union full-size preview (slow)",
                        variable=self.var_union).grid(row=r, column=0, columnspan=3,
                                                      sticky="w", **pad)

        mod = ttk.LabelFrame(cols, text="3 - printable scale model", padding=8)
        mod.pack(side="left", fill="both", expand=True, padx=(10, 0))
        ttk.Checkbutton(mod, text="build a scale model STL", variable=self.var_model,
                        command=self._sync_model).grid(row=0, column=0, columnspan=3,
                                                       sticky="w", **pad)
        self.model_widgets = []
        ttk.Label(mod, text="printer").grid(row=1, column=0, sticky="w", **pad)
        w = ttk.Combobox(mod, textvariable=self.var_printer, width=12, state="readonly",
                         values=sorted(scale.PRINTERS))
        w.grid(row=1, column=1, sticky="w", **pad)
        w.bind("<<ComboboxSelected>>", lambda _e: self._show_volume())
        self.model_widgets.append(w)
        self.lbl_volume = ttk.Label(mod, text="", foreground="#888")
        self.lbl_volume.grid(row=1, column=2, sticky="w")

        ttk.Label(mod, text="ratio 1:N").grid(row=2, column=0, sticky="w", **pad)
        w = ttk.Entry(mod, textvariable=self.var_ratio, width=10)
        w.grid(row=2, column=1, sticky="w", **pad)
        self.model_widgets.append(w)
        ttk.Label(mod, text="'auto' = biggest that fits", foreground="#888").grid(
            row=2, column=2, sticky="w")

        ttk.Label(mod, text="min strut (mm)").grid(row=3, column=0, sticky="w", **pad)
        w = ttk.Entry(mod, textvariable=self.var_minstrut, width=10)
        w.grid(row=3, column=1, sticky="w", **pad)
        self.model_widgets.append(w)
        ttk.Label(mod, text="blank = 3 x nozzle", foreground="#888").grid(
            row=3, column=2, sticky="w")

        ttk.Label(mod, text="base plate (mm)").grid(row=4, column=0, sticky="w", **pad)
        w = ttk.Entry(mod, textvariable=self.var_base, width=10)
        w.grid(row=4, column=1, sticky="w", **pad)
        self.model_widgets.append(w)
        ttk.Label(mod, text="0 = none", foreground="#888").grid(row=4, column=2, sticky="w")

        w = ttk.Checkbutton(mod, text="watertight union (slower, safest slicing)",
                            variable=self.var_model_union)
        w.grid(row=5, column=0, columnspan=3, sticky="w", **pad)
        self.model_widgets.append(w)
        w = ttk.Checkbutton(mod, text="allow scaling UP to fill the plate",
                            variable=self.var_enlarge)
        w.grid(row=6, column=0, columnspan=3, sticky="w", **pad)
        self.model_widgets.append(w)

        # ---- outputs ----------------------------------------------------
        out = ttk.LabelFrame(root, text="4 - outputs", padding=8)
        out.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(out, text="full-size strut solid", variable=self.var_preview
                        ).grid(row=0, column=0, sticky="w", **pad)
        ttk.Checkbutton(out, text="3D viewer", variable=self.var_viewer
                        ).grid(row=0, column=1, sticky="w", **pad)
        ttk.Checkbutton(out, text="robot waypoints CSV", variable=self.var_waypoints
                        ).grid(row=0, column=2, sticky="w", **pad)
        ttk.Checkbutton(out, text="Yaskawa .JBI jobs", variable=self.var_jbi
                        ).grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(out, text="folder").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(out, textvariable=self.var_out).grid(row=1, column=1, columnspan=3,
                                                       sticky="we", **pad)
        ttk.Button(out, text="Browse...", command=self.browse_out).grid(row=1, column=4, **pad)
        out.columnconfigure(2, weight=1)

        # ---- run bar -----------------------------------------------------
        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(10, 4))
        self.btn_run = ttk.Button(bar, text="Run formgen", command=self.start_run)
        self.btn_run.pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=220)
        self.progress.pack(side="left", padx=10)
        ttk.Label(bar, textvariable=self.var_status).pack(side="left")

        self.btn_model = ttk.Button(bar, text="Open model STL", state="disabled",
                                    command=lambda: _open_path(self._out("model_stl")))
        self.btn_model.pack(side="right", padx=4)
        self.btn_viewer = ttk.Button(bar, text="Open 3D viewer", state="disabled",
                                     command=lambda: _open_path(self._out("viewer_html")))
        self.btn_viewer.pack(side="right", padx=4)
        self.btn_folder = ttk.Button(bar, text="Open folder", state="disabled",
                                     command=lambda: _open_path(self.var_out.get()))
        self.btn_folder.pack(side="right", padx=4)

        # ---- log ---------------------------------------------------------
        logf = ttk.LabelFrame(root, text="log", padding=4)
        logf.pack(fill="both", expand=True)
        self.txt = tk.Text(logf, height=14, wrap="none", bg="#0e1116", fg="#dce3ec",
                           insertbackground="#dce3ec", font=("Consolas", 9))
        self.txt.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logf, command=self.txt.yview)
        sb.pack(side="right", fill="y")
        self.txt.configure(yscrollcommand=sb.set)

        self._sync_source()
        self._sync_model()
        self._show_volume()
        self.log("formgen Studio -- pick geometry, then Run.")

    # ------------------------------------------------------------- helpers
    def _out(self, key: str) -> str:
        return (self.result or {}).get("outputs", {}).get(key, "")

    def log(self, msg: str) -> None:
        self.log_q.put(str(msg))

    def _drain_log(self):
        try:
            while True:
                line = self.log_q.get_nowait()
                self.txt.insert("end", line + "\n")
                self.txt.see("end")
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    def _sync_source(self):
        fusion = self.var_source.get() == "fusion"
        self.cb_body.configure(state="readonly" if fusion else "disabled")

    def _sync_model(self):
        state = "normal" if self.var_model.get() else "disabled"
        for w in self.model_widgets:
            try:
                w.configure(state=state if not isinstance(w, ttk.Combobox) else
                            ("readonly" if state == "normal" else "disabled"))
            except Exception:
                pass

    def _show_volume(self):
        try:
            spec = scale.printer_spec(self.var_printer.get())
            self.lbl_volume.configure(
                text="{:.0f} x {:.0f} x {:.0f} mm".format(*spec["volume"]))
        except Exception:
            self.lbl_volume.configure(text="")

    # ------------------------------------------------------------- actions
    def browse_input(self):
        path = filedialog.askopenfilename(
            title="Pick a Fusion export",
            filetypes=[("CAD / mesh", "*.step *.stp *.stl *.obj *.ply *.3mf *.glb"),
                       ("All files", "*.*")])
        if path:
            self.var_input.set(path)
            self.var_source.set("file")
            self._sync_source()

    def browse_out(self):
        path = filedialog.askdirectory(title="Output folder")
        if path:
            self.var_out.set(path)

    def refresh_fusion(self):
        """Ask the live Fusion session what bodies it has (never blocks the UI)."""
        def work():
            try:
                from . import fusion

                br = fusion.FusionBridge()
                if not br.ping():
                    self.after(0, lambda: self.var_fusion_status.set(
                        "Fusion bridge not answering on 127.0.0.1:9876 "
                        "(start Fusion + the Fusion360MCP add-in)"))
                    return
                design = br.design_name()
                bodies = br.list_bodies()
                sel = br.selection()
                names = [SELECTION_LABEL] + [
                    "{}   [{:.0f} x {:.0f} x {:.0f} mm]".format(b["ref"], *b["size_mm"])
                    for b in bodies]

                def apply():
                    self.fusion_bodies = bodies
                    self.cb_body.configure(values=names)
                    if self.var_body.get() not in names:
                        self.var_body.set(SELECTION_LABEL)
                    self.var_fusion_status.set(
                        "connected - '{}': {} visible bodies, {} selected".format(
                            design, len(bodies), len(sel)))
                self.after(0, apply)
            except Exception as exc:
                msg = "Fusion check failed: {}".format(exc)
                self.after(0, lambda: self.var_fusion_status.set(msg))

        threading.Thread(target=work, daemon=True).start()

    def _collect(self) -> Options:
        def f(var, default):
            try:
                return float(str(var.get()).strip())
            except Exception:
                return default

        ref = None
        if self.var_source.get() == "fusion":
            label = self.var_body.get()
            if label and label != SELECTION_LABEL:
                ref = label.split("   [")[0]

        ratio_raw = str(self.var_ratio.get()).strip().lower()
        ratio = None
        if ratio_raw and ratio_raw not in ("auto", "fit", ""):
            try:
                ratio = float(ratio_raw.lstrip("1:").lstrip(":")) or None
            except Exception:
                ratio = None
        min_strut = None
        if str(self.var_minstrut.get()).strip():
            min_strut = f(self.var_minstrut, None)

        return Options(
            source=self.var_source.get(),
            input=self.var_input.get().strip(),
            fusion_ref=ref,
            mode=self.var_mode.get(),
            strut=f(self.var_strut, 60.0),
            thickness=f(self.var_thickness, 80.0),
            strut_dia=f(self.var_dia, 8.0),
            density=f(self.var_density, 1.10),
            speed=f(self.var_speed, 25.0),
            union=bool(self.var_union.get()),
            out=self.var_out.get().strip() or "out",
            want_preview=bool(self.var_preview.get()),
            want_viewer=bool(self.var_viewer.get()),
            want_waypoints=bool(self.var_waypoints.get()),
            want_jbi=bool(self.var_jbi.get()),
            model=bool(self.var_model.get()),
            printer=self.var_printer.get(),
            ratio=ratio,
            min_strut=min_strut,
            base_mm=f(self.var_base, 0.0),
            model_union=bool(self.var_model_union.get()),
            allow_enlarge=bool(self.var_enlarge.get()),
        )

    def start_run(self):
        if self.worker and self.worker.is_alive():
            return
        opts = self._collect()
        if opts.source == "file" and not os.path.exists(opts.input):
            messagebox.showerror(APP_TITLE, "Pick a geometry file first.")
            return

        self.result = None
        for b in (self.btn_model, self.btn_viewer, self.btn_folder):
            b.configure(state="disabled")
        self.txt.delete("1.0", "end")
        self.btn_run.configure(state="disabled")
        self.progress.start(12)
        self.var_status.set("running...")

        def work():
            try:
                res = run(opts, log=self.log)
                self.after(0, lambda: self._done(res, None))
            except Exception as exc:
                self.log(traceback.format_exc())
                self.after(0, lambda: self._done(None, exc))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _done(self, res, exc):
        self.progress.stop()
        self.btn_run.configure(state="normal")
        if exc is not None:
            self.var_status.set("failed")
            messagebox.showerror(APP_TITLE, "{}: {}".format(type(exc).__name__, exc))
            return
        self.result = res
        plan = res.get("scale_model")
        bits = ["{} struts".format(res["stats"]["struts"]),
                "{} kg full size".format(res["estimate"]["material_mass_kg"])]
        if plan:
            bits.append("model {} ({} mm)".format(
                plan["ratio_label"], " x ".join(str(v) for v in plan["model_size_mm"])))
        self.var_status.set(" | ".join(bits))
        self.btn_folder.configure(state="normal")
        if self._out("viewer_html"):
            self.btn_viewer.configure(state="normal")
        if self._out("model_stl"):
            self.btn_model.configure(state="normal")


def main() -> int:
    Studio(ui_scale=_enable_hidpi()).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
