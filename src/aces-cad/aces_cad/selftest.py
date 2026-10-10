"""aces-cad selftest: the whole loop end to end in a throwaway ACES_WORK, ACES_STATE and ACES_USER_CONFIG (spec
section 18), so the user's real ledger and projects are never touched.

It creates the project `selftest` (class sovereign) with `aces project new` when the aces CLI is there, else writes
an aces-base-format aces.yaml, then runs: 1 doctor; 2 the ACES plate; 3 a parameter override; 4 inspect (mass in
AlSi10Mg, the through hole, wall thickness, validity); 5 an iso render; 6 a drawing (dimension texts and DXF audit);
7 a pull (every sha256 against source.json, the ledger entry when the aces CLI offers it, `aces ledger verify`); 8 a
second run and pull with the same geometry_sha256; 9 hostile scripts (a socket, an endless loop, a write outside
the run directory, and an OCCT STEP export outside it, which only strict mode can stop). --quick runs 1-4.

Every line is PASS, FAIL or SKIP with the measured value; the exit code is 0 when nothing failed, else 1. Expected
numbers are the spec's probe values (section 2.3). Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, errors

PROJECT = "selftest"
PLATE_VOLUME = 11607.300918301276        # 60*40*5 - pi*25*5
PLATE_70_VOLUME = 13607.300918301276     # length 70
PLATE_COUNTS = {"solids": 1, "shells": 1, "faces": 7, "edges": 15, "vertices": 10}
PLATE_MASS_KG = 0.03099149345186441      # AlSi10Mg, 2670 kg/m3
HOLE_ENTRY = (30.0, 20.0, 5.0)

ACES_PLATE = '''from build123d import Align, Box, Cylinder, Pos

PARAMS = {
    "length": {"default": 60.0, "min": 10.0, "max": 500.0, "unit": "mm", "doc": "plate length along X"},
    "width": {"default": 40.0, "min": 10.0, "max": 500.0, "unit": "mm", "doc": "plate width along Y"},
    "thickness": {"default": 5.0, "min": 1.0, "max": 50.0, "unit": "mm", "doc": "plate thickness along Z"},
    "hole_d": {"default": 10.0, "min": 1.0, "max": 30.0, "unit": "mm", "doc": "centre hole diameter"},
}


def build(p):
    plate = Box(p.length, p.width, p.thickness, align=(Align.MIN, Align.MIN, Align.MIN))
    hole = Cylinder(p.hole_d / 2, p.thickness, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return plate - Pos(p.length / 2, p.width / 2, 0) * hole
'''

HOSTILE_SOCKET = '''import socket

from build123d import Box

PARAMS = {"port": {"default": 9, "min": 1, "max": 65535, "doc": "a loopback port"}}


def build(p):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", p.port))
    return Box(1, 1, 1)
'''

HOSTILE_LOOP = '''from build123d import Box


def build(p):
    while True:
        pass
    return Box(1, 1, 1)
'''

HOSTILE_WRITE = '''from build123d import Box

PARAMS = {"target": {"default": "", "doc": "a path outside the run directory"}}


def build(p):
    with open(p.target, "w") as fh:
        fh.write("aces-cad selftest: this file must not exist")
    return Box(1, 1, 1)
'''

HOSTILE_OCCT = '''from build123d import Box, export_step

PARAMS = {"target": {"default": "", "doc": "a path outside the run directory"}}


def build(p):
    box = Box(10, 10, 10)
    export_step(box, p.target)
    return box
'''

_MISSING = object()


def find(obj, key):
    """The first value under `key` in a nested result, depth first (check outputs nest per part)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        values = obj.values()
    elif isinstance(obj, list):
        values = obj
    else:
        return _MISSING
    for v in values:
        hit = find(v, key)
        if hit is not _MISSING:
            return hit
    return _MISSING


def _close(a, b, tol: float) -> bool:
    return isinstance(a, (int, float)) and not isinstance(a, bool) and abs(float(a) - float(b)) <= tol


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Check(Exception):
    """A failed expectation; the message is the FAIL detail."""


class Skip(Exception):
    """A check that cannot run here; the message is the reason."""


class Report:
    def __init__(self, as_json: bool):
        self.as_json = as_json
        self.rows: list = []

    def add(self, n: str, name: str, status: str, detail: str) -> None:
        self.rows.append({"n": n, "name": name, "status": status, "detail": detail})
        if not self.as_json:
            sys.stdout.write(f"{status:4s}  {n:>2s} {name}: {detail}\n")
            sys.stdout.flush()

    def count(self, status: str) -> int:
        return sum(1 for r in self.rows if r["status"] == status)


class Selftest:
    def __init__(self, env: dict, report: Report, work: Path):
        self.env = env
        self.report = report
        self.work = work
        self.state: dict = {}

    def dispatch(self, name: str, kwargs: dict) -> dict:
        from . import ops

        return ops.dispatch(name, dict(kwargs), env=self.env)

    def run(self, n: str, name: str, fn) -> bool:
        try:
            detail = fn()
        except Skip as e:
            self.report.add(n, name, "SKIP", str(e))
            return False
        except Check as e:
            self.report.add(n, name, "FAIL", str(e))
            return False
        except errors.CadError as e:
            self.report.add(n, name, "FAIL", f"{e.kind}: {e}")
            return False
        except Exception as e:  # noqa: BLE001 (a selftest reports, it does not crash)
            self.report.add(n, name, "FAIL", f"internal: {type(e).__name__}: {e}")
            return False
        self.report.add(n, name, "PASS", detail)
        return True

    # --- the checks ---------------------------------------------------------------------------------------------

    def doctor(self) -> str:
        r = self.dispatch("doctor", {})
        self.state["doctor"] = r
        sb = r.get("sandbox") if isinstance(r.get("sandbox"), dict) else {}
        missing = ", ".join(sb.get("missing") or []) or "nothing"
        detail = f"verdict {r.get('verdict')}, mode {r.get('mode')}, sandbox missing {missing}"
        if r.get("verdict") != "ready":
            failing = [c.get("check") for c in r.get("checks") or [] if c.get("pass") is False]
            raise Check(detail + (f"; failing: {', '.join(map(str, failing))}" if failing else "") +
                        " (run aces-cad doctor)")
        return detail

    def _plate(self, params: dict | None, name: str) -> dict:
        kw = {"script": ACES_PLATE, "name": name, "project": PROJECT}
        if params:
            kw["params"] = params
        r = self.dispatch("run", kw)
        if r.get("status") != "ok":
            raise Check(f"run status {r.get('status')}")
        parts = (r.get("result") or {}).get("parts") or []
        if len(parts) != 1:
            raise Check(f"expected one part, got {len(parts)}")
        return r

    def plate(self) -> str:
        r = self._plate(None, "plate")
        p = r["result"]["parts"][0]
        self.state["plate"] = r["run_id"]
        if not _close(p.get("volume_mm3"), PLATE_VOLUME, 1e-6):
            raise Check(f"volume {p.get('volume_mm3')} mm3, expected {PLATE_VOLUME} +- 1e-6")
        counts = {k: (p.get("counts") or {}).get(k) for k in PLATE_COUNTS}
        if counts != PLATE_COUNTS:
            raise Check(f"counts {counts}, expected {PLATE_COUNTS}")
        if p.get("valid") is not True:
            raise Check(f"valid {p.get('valid')} (reasons {p.get('reasons')})")
        return f"run {r['run_id']}: volume {p['volume_mm3']!r} mm3, counts 1/1/7/15/10, valid"

    def override(self) -> str:
        r = self._plate({"length": 70.0}, "plate70")
        vol = r["result"]["parts"][0].get("volume_mm3")
        if not _close(vol, PLATE_70_VOLUME, 1e-6):
            raise Check(f"length=70 gave {vol} mm3, expected {PLATE_70_VOLUME} +- 1e-6")
        if r.get("params_overridden") != ["length"]:
            raise Check(f"params_overridden {r.get('params_overridden')}, expected ['length']")
        return f"run {r['run_id']}: length=70 -> {vol!r} mm3"

    def _need(self, key: str) -> str:
        if key not in self.state:
            raise Skip("the ACES plate run did not pass")
        return self.state[key]

    def inspect(self) -> str:
        run_id = self._need("plate")
        r = self.dispatch("inspect", {"run_id": run_id, "checks": ["mass", "validity", "holes", "thickness"],
                                      "material": "AlSi10Mg", "project": PROJECT})
        checks = r.get("checks") or {}
        problems = [f"{k}: {v['error'].get('message')}" for k, v in checks.items()
                    if isinstance(v, dict) and isinstance(v.get("error"), dict)]
        if problems:
            raise Check("; ".join(problems))
        mass = find(checks.get("mass"), "mass_kg")
        if not (isinstance(mass, (int, float)) and abs(mass - PLATE_MASS_KG) <= 1e-9 * PLATE_MASS_KG):
            raise Check(f"mass {mass!r} kg, expected {PLATE_MASS_KG} (relative 1e-9)")
        holes = find(checks.get("holes"), "holes")
        if not isinstance(holes, list) or len(holes) != 1:
            raise Check(f"expected one hole, got {holes if not isinstance(holes, list) else len(holes)}")
        h = holes[0]
        entry = h.get("entry_point_mm") or []
        if not (h.get("through") is True and _close(h.get("diameter_mm"), 10.0, 1e-6) and len(entry) == 3
                and all(_close(a, b, 1e-6) for a, b in zip(entry, HOLE_ENTRY))):
            raise Check(f"hole {h}: expected a through 10 mm hole entering at {HOLE_ENTRY}")
        tmin = find(checks.get("thickness"), "min_mm")
        if not _close(tmin, 5.0, 0.01):
            raise Check(f"thickness min {tmin!r} mm, expected 5.0 +- 0.01")
        ok = find(checks.get("validity"), "ok")
        if ok is not True:
            raise Check(f"validity ok {ok!r}")
        return (f"mass {mass!r} kg, one through \u00d810 hole at {tuple(entry)}, thickness min {tmin!r} mm, "
                f"validity ok")

    def render(self) -> str:
        run_id = self._need("plate")
        r = self.dispatch("render", {"run_id": run_id, "views": ["iso"], "size": "small", "inline": False,
                                     "project": PROJECT})
        renders = r.get("renders") or []
        if len(renders) != 1:
            raise Check(f"expected one render, got {len(renders)} ({r.get('warnings')})")
        x = renders[0]
        path = Path(str(x.get("path")))
        if (x.get("width_px"), x.get("height_px")) != (640, 500) or not path.is_file() or path.stat().st_size == 0:
            raise Check(f"render {x}: expected a 640 x 500 PNG on disk")
        return f"{path} (640 x 500 px, {x.get('bytes')} bytes)"

    def drawing(self) -> str:
        run_id = self._need("plate")
        r = self.dispatch("drawing", {"run_id": run_id, "project": PROJECT})
        texts = [str(d.get("text")) for d in r.get("dimensions") or []]
        wanted = ["60", "40", "5", "\u00d810 THRU"]
        missing = [t for t in wanted if t not in texts]
        if missing:
            raise Check(f"dimension texts {texts} lack {missing}")
        if r.get("audit_errors") != 0:
            raise Check(f"DXF audit errors: {r.get('audit_errors')}")
        files = r.get("files") or {}
        for fmt in ("dxf", "svg", "png"):
            info = files.get(fmt) or {}
            if not Path(str(info.get("path"))).is_file():
                raise Check(f"no {fmt} file ({info})")
        return f"dimensions {', '.join(texts)}; audit 0; {files['dxf']['path']}"

    def _pull_and_verify(self, run_id: str) -> dict:
        r = self.dispatch("pull", {"run_id": run_id, "project": PROJECT})
        bundle = Path(str(r.get("bundle")))
        source_bytes = (bundle / "source.json").read_bytes()
        source = json.loads(source_bytes.decode("utf-8"))
        outcome = json.loads((bundle / "outcome.json").read_bytes().decode("utf-8"))
        bad = []
        for name, info in (source.get("files") or {}).items():
            path = bundle / name
            if not path.is_file() or _sha(path) != info.get("sha256") or path.stat().st_size != info.get("bytes"):
                bad.append(name)
        if hashlib.sha256(source_bytes).hexdigest() != ((outcome.get("files") or {}).get("source.json") or
                                                        {}).get("sha256"):
            bad.append("source.json (outcome.json)")
        if bad:
            raise Check(f"sha256 mismatch in {bundle}: {', '.join(bad)}")
        if r.get("geometry_sha256") != (source.get("files") or {}).get("part.step", {}).get("sha256"):
            raise Check("geometry_sha256 is not the sha256 of part.step")
        r["_source"] = source
        return r

    def pull(self) -> str:
        r = self._pull_and_verify(self._need("plate"))
        self.state["geometry"] = r["geometry_sha256"]
        offered = (r["_source"].get("ledger") or {}).get("available")
        ledger = r.get("ledger") or {}
        detail = f"{len(r['_source'].get('files') or {})} files match source.json; geometry {r['geometry_sha256']}"
        if not offered:
            return detail + f"; ledger not offered here ({(r['_source'].get('ledger') or {}).get('reason')})"
        if not ledger.get("written"):
            raise Check(f"the aces CLI offers ledger record but nothing was written: {ledger.get('reason')}")
        from . import project

        done = project.run_aces(["ledger", "verify"], env=self.env)
        if done is None or done.returncode != 0:
            out = (done.stdout + done.stderr).decode("utf-8", "replace").strip() if done is not None else "no aces"
            raise Check(f"aces ledger verify failed: {out}")
        return detail + f"; cad.pull is ledger entry {ledger.get('n')}; aces ledger verify ok"

    def reproducible(self) -> str:
        first = self._need("geometry")
        r = self._plate(None, "plate")
        again = self._pull_and_verify(r["run_id"])
        if again["geometry_sha256"] != first:
            raise Check(f"second pull geometry {again['geometry_sha256']} differs from {first}")
        return f"run {r['run_id']} pulled with the same geometry_sha256"

    def _expect_refusal(self, script: str, kinds: tuple, params: dict | None = None, timeout_s: int | None = None):
        kw = {"script": script, "name": "hostile", "project": PROJECT}
        if params:
            kw["params"] = params
        if timeout_s:
            kw["timeout_s"] = timeout_s
        t0 = time.monotonic()
        try:
            self.dispatch("run", kw)
        except errors.CadError as e:
            if e.kind in kinds:
                return f"refused: {e.kind} (exit {e.exit_code}) after {time.monotonic() - t0:.1f} s"
            raise Check(f"expected {' or '.join(kinds)}, got {e.kind}: {e}") from None
        raise Check(f"the run succeeded; expected {' or '.join(kinds)}")

    def hostile_socket(self) -> str:
        return self._expect_refusal(HOSTILE_SOCKET, ("sandbox",))

    def hostile_loop(self) -> str:
        return self._expect_refusal(HOSTILE_LOOP, ("timeout",), timeout_s=5)

    def hostile_write(self) -> str:
        target = self.work / f"outside-{secrets.token_hex(4)}.txt"
        detail = self._expect_refusal(HOSTILE_WRITE, ("sandbox",), params={"target": str(target)})
        if target.exists():
            raise Check(f"{target} was written")
        return detail + "; the file is absent"

    def hostile_occt(self) -> str:
        doctor = self.state.get("doctor") or {}
        sb = doctor.get("sandbox") if isinstance(doctor.get("sandbox"), dict) else {}
        landlock = sb.get("landlock") if isinstance(sb.get("landlock"), dict) else {}
        if doctor.get("mode") != "strict" or landlock.get("active") is not True:
            raise Skip(f"mode {doctor.get('mode')}: without Landlock nothing stops OCCT's own file output (C++ I/O "
                       f"bypasses the audit hook), so this script is not run")
        target = self.work / f"escape-{secrets.token_hex(4)}.step"
        try:
            self.dispatch("run", {"script": HOSTILE_OCCT, "name": "hostile", "params": {"target": str(target)},
                                  "project": PROJECT})
        except errors.CadError as e:
            if target.exists():
                raise Check(f"{target} was written although the run failed ({e.kind})") from None
            return f"the export failed ({e.kind}: {str(e)[:120]}) and the file is absent"
        raise Check(f"the run succeeded (file {'present' if target.exists() else 'absent'}); expected a refusal")


def _make_project(env: dict, work: Path) -> str:
    from . import project

    done = project.run_aces(["project", "new", PROJECT, "--class", "sovereign", "--hypothesis",
                             "aces-cad selftest"], env=env)
    if done is not None and done.returncode == 0:
        return "aces project new"
    path = work / PROJECT / "aces.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(f"name: {PROJECT}\nclass: sovereign\ncreated: \"{created}\"\ncompany: \"\"\nrd_activity: \"\"\n"
                    f"hypothesis: \"aces-cad selftest\"\ncad_sources: []\ncomponents: []\nevidence: []\n",
                    encoding="utf-8", newline="\n")
    why = "no aces CLI" if done is None else f"aces project new exited {done.returncode}"
    return f"aces.yaml written directly ({why})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aces-cad selftest", description=__doc__.split("\n\n")[0])
    parser.add_argument("--quick", action="store_true", help="checks 1-4 only")
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory")
    parser.add_argument("--json", action="store_true", help="one JSON document instead of PASS/FAIL/SKIP lines")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    t0 = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="aces-cad-selftest-"))
    work = tmp / "work"
    for d in (work, tmp / "state", tmp / "config"):
        d.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "ACES_CAD_PROJECT"}
    env.update(ACES_WORK=str(work), ACES_STATE=str(tmp / "state"), ACES_USER_CONFIG=str(tmp / "config"))
    report = Report(args.json)
    if not args.json:
        sys.stdout.write(f"aces-cad {__version__} selftest in {tmp} (ACES_CAD_SANDBOX "
                         f"{env.get('ACES_CAD_SANDBOX') or 'strict'})\n")
    test = Selftest(env, report, tmp)
    try:
        try:
            how = _make_project(env, work)
            report.add("0", "project", "PASS", f"{PROJECT} (sovereign) by {how}")
        except Exception as e:  # noqa: BLE001
            report.add("0", "project", "FAIL", f"{type(e).__name__}: {e}")
        steps = [("1", "doctor", test.doctor), ("2", "ACES plate", test.plate), ("3", "parameter override",
                 test.override), ("4", "inspect", test.inspect)]
        if not args.quick:
            steps += [("5", "render", test.render), ("6", "drawing", test.drawing), ("7", "pull", test.pull),
                      ("8", "reproducible pull", test.reproducible), ("9a", "hostile socket", test.hostile_socket),
                      ("9b", "hostile loop", test.hostile_loop), ("9c", "hostile write outside", test.hostile_write),
                      ("9d", "hostile OCCT export outside", test.hostile_occt)]
        ready = True
        for n, name, fn in steps:
            if not ready:
                report.add(n, name, "SKIP", "the doctor is not ready: run aces-cad doctor")
                continue
            ok = test.run(n, name, fn)
            if n == "1" and not ok:
                ready = False
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
    wall = round(time.monotonic() - t0, 1)
    passed, failed, skipped = report.count("PASS"), report.count("FAIL"), report.count("SKIP")
    if args.json:
        sys.stdout.write(json.dumps({"ok": failed == 0, "checks": report.rows, "passed": passed, "failed": failed,
                                     "skipped": skipped, "wall_s": wall, "work_dir": str(tmp), "kept": args.keep},
                                    indent=1, ensure_ascii=True) + "\n")
    else:
        sys.stdout.write(f"selftest: {passed} passed, {failed} failed, {skipped} skipped in {wall} s; "
                         f"{'kept' if args.keep else 'removed'} {tmp}\n")
    sys.stdout.flush()
    return 0 if failed == 0 else 1

