from __future__ import annotations

import os


class Adapter:
    name = "nozzle-angelino"

    def inputs(self, case: dict, spec) -> list:
        return []

    def run(self, case: dict, spec, run_dir: str) -> dict:
        from gendesign.nozzle import angelino_contour, ideal_performance, truncate, write_contour

        gamma = float(case["gamma"])
        eps = float(case["eps"])
        re = float(case["re_mm"])
        c = angelino_contour(gamma, eps, re, int(case.get("n", 200)))
        if float(case.get("trunc", 1.0)) < 1.0:
            c = truncate(c, float(case["trunc"]))
        if all(k in case for k in ("pc_bar", "tc_K", "mw")):
            c["performance_ideal"] = ideal_performance(gamma, eps, c["Me"], float(case["pc_bar"]),
                                                       float(case["tc_K"]), float(case["mw"]), c["At_mm2"])
        out = os.path.join(run_dir, "nozzle")
        meta = write_contour(c, out, float(case.get("upstream_mm", 20.0)), not case.get("no_step", False))
        m = {k: meta[k] for k in ("Me", "nu_e_deg", "At_mm2", "throat_gap_mm", "throat_area_check", "L_full_mm")}
        for k in ("L_trunc_mm", "base_r_mm"):
            if k in meta:
                m[k] = meta[k]
        if "performance_ideal" in meta:
            m.update({k: v for k, v in meta["performance_ideal"].items()})
        arts = [os.path.join(out, v) for k, v in meta["outputs"].items() if not k.endswith("_error")]
        m["artifacts"] = [a if os.path.isabs(a) else os.path.join(out, os.path.basename(a)) for a in arts]
        return m
