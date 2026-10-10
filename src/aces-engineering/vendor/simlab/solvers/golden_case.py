"""golden-case: thin shim; the body is aces_engineering.golden.rigside in the aces-golden image (imported in run(),
never at module level: the queue and MCP containers import every adapter to read its stages and lack aces_engineering)."""
from __future__ import annotations

SOLVER = "golden-case"           # golden_case.py: "golden-case"; ccx_check.py: "ccx-check"


class Adapter:
    name = SOLVER

    def inputs(self, case: dict, spec) -> list:
        files = ((case.get("golden") or {}).get("files") or {})
        return [spec.resolve(f["path"]) for f in files.values() if isinstance(f, dict) and f.get("path")]

    def run(self, case: dict, spec, run_dir: str) -> dict:
        from aces_engineering.golden import rigside

        return rigside.run(case, spec, run_dir, SOLVER)
