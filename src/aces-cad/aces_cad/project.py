"""The class gate, the bound project and the bridge to the aces CLI.

aces-cad is local: nothing leaves the machine, so every policy class (personal, open, commercial, sovereign,
controlled) is allowed. The gate still runs on every tool, so the project exists and every record and ledger entry
carries its class. The class comes from `aces project show --json NAME` when the aces CLI is available, else from the
single top-level `class:` line of <ACES_WORK>/<name>/aces.yaml (two lines are refused). An unknown or null class is
refused with the `aces project classify` fix.

ACES_CAD_PROJECT binds the CLI and the MCP server to one project: a different project is refused. The aces CLI is
ACES_CAD_ACES_CMD (a JSON array of strings, or a single path; [] means none) or `aces` on PATH. It runs with ACES_WORK
set to config.work_dir() and without ONSHAPE_* variables. `ledger record` and `project add-cad-source` are probed
first; aces-base 0.1.0 has neither, and the results say plainly when nothing was written.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Mapping

from . import config
from .errors import ProjectError

CLASSES = ("personal", "open", "commercial", "sovereign", "controlled")
PROJECT_NAME_RE = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
NO_ACES = "no aces CLI on PATH (or ACES_CAD_ACES_CMD=[])"


def _environ(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def aces_command(env: Mapping[str, str] | None = None) -> list[str] | None:
    """The aces CLI as an argv prefix, or None when there is none."""
    return config.aces_command(env)


def run_aces(args: list[str], *, input_bytes: bytes | None = None, env: Mapping[str, str] | None = None,
             timeout: float = 30):
    """Run the aces CLI (CompletedProcess), or None when there is no aces CLI.

    ACES_WORK is set to aces-cad's own project root (config.work_dir), so `aces project show`, `ledger record` and
    `project add-cad-source` look for projects where aces-cad writes; ONSHAPE_* variables are removed. Without
    input_bytes the child's stdin is the null device, never this process's stdin: under aces-cad-mcp that is the
    JSON-RPC pipe, which a child could read from, and on Windows a child that inherits a pipe the server's reader
    thread is blocked on hangs until the 30 s timeout.
    """
    cmd = aces_command(env)
    if not cmd:
        return None
    child = {k: v for k, v in _environ(env).items() if not k.startswith("ONSHAPE_")}
    child["ACES_WORK"] = str(config.work_dir(env))
    stdin = {"input": input_bytes} if input_bytes is not None else {"stdin": subprocess.DEVNULL}
    try:
        return subprocess.run(cmd + list(args), capture_output=True, timeout=timeout, env=child, **stdin)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd + list(args), 124, b"", f"timed out after {timeout} s".encode())
    except OSError:
        return None


def _first_line(data: bytes | None) -> str:
    text = (data or b"").decode("utf-8", "replace").strip()
    return text.splitlines()[0].strip()[:300] if text else "(no output)"


def validate_project_name(name) -> str:
    if not isinstance(name, str) or not re.fullmatch(PROJECT_NAME_RE, name):
        shown = repr(name) if len(repr(name)) <= 80 else repr(name)[:77] + "..."
        raise ProjectError(f"invalid project name {shown}: use letters, digits, '.', '_' and '-' (at most 64 "
                           f"characters, starting with a letter or digit)")
    return name


def bound_project(env: Mapping[str, str] | None = None) -> str | None:
    value = (_environ(env).get("ACES_CAD_PROJECT") or "").strip()
    return validate_project_name(value) if value else None


def resolve_project(name, env: Mapping[str, str] | None = None) -> str:
    """The project a call is for: `name`, or the bound project; never a project other than the bound one."""
    bound = bound_project(env)
    if bound and name and name != bound:
        raise ProjectError(f"this aces-cad is bound to project {bound} (ACES_CAD_PROJECT); refusing project {name}")
    result = name or bound
    if not result:
        raise ProjectError("no ACES project given: pass --project NAME (MCP: the project argument) or set "
                           "ACES_CAD_PROJECT")
    return validate_project_name(result)


def _unscalar(value: str):
    """aces-base's reading of a scalar, for the class line: null and empty are None; double quotes are unescaped."""
    if value in ("", "null"):
        return None
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def project_class(name, env: Mapping[str, str] | None = None) -> dict:
    """{"name", "class", "class_source", "dir"} from `aces project show --json NAME`, else from aces.yaml."""
    name = validate_project_name(name)
    pdir = config.work_dir(env) / name
    shown = run_aces(["project", "show", "--json", name], env=env)
    if shown is not None and shown.returncode == 0:
        try:
            data = json.loads(shown.stdout.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            cls = data.get("class")
            return {"name": name, "class": cls if isinstance(cls, str) else None,
                    "class_source": "aces project show", "dir": str(pdir)}
    path = pdir / "aces.yaml"
    if path.is_file():
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise ProjectError(f"cannot read {path}: {getattr(e, 'strerror', None) or type(e).__name__}") from None
        lines = [line for line in text.splitlines() if line.startswith("class:")]
        if len(lines) > 1:
            raise ProjectError(f"{path} has {len(lines)} top-level class lines; refusing to guess the project class")
        cls = _unscalar(lines[0][len("class:"):].strip()) if lines else None
        return {"name": name, "class": cls, "class_source": "aces.yaml", "dir": str(pdir)}
    raise ProjectError(f"no ACES project '{name}' (looked for {path}): create it with aces project new {name} "
                       f"--class <personal|open|commercial|sovereign|controlled>")


def gate(project: str | None, env: Mapping[str, str] | None = None) -> dict:
    """The class gate: {"name", "class", "class_source", "dir"}; ProjectError when it fails.

    Every class is allowed (nothing leaves the machine); a class outside the five policy classes, or none, is refused.
    """
    name = resolve_project(project, env)
    info = project_class(name, env)
    cls = info["class"]
    if cls not in CLASSES:
        shown = "none" if cls is None else cls
        raise ProjectError(f"project {name} has class '{shown}', which is not a policy class: fix it with aces "
                           f"project classify {name} <class> --reason ...")
    return info


def ledger_capability(env: Mapping[str, str] | None = None) -> dict:
    """Whether the aces CLI offers `ledger record`, from `aces ledger record --help`: {"available", "reason",
    "print_json"}."""
    if not aces_command(env):
        return {"available": False, "reason": NO_ACES, "print_json": False}
    probe = run_aces(["ledger", "record", "--help"], env=env)
    if probe is None:
        return {"available": False, "reason": NO_ACES, "print_json": False}
    if probe.returncode != 0:
        if probe.returncode == 124 and (probe.stderr or b"").startswith(b"timed out"):
            reason = f"aces ledger record --help {_first_line(probe.stderr)}; the OS ledger entry was not written"
        else:
            reason = ("the installed aces CLI has no ledger record command (aces-base 0.1.0); the OS ledger entry was "
                      "not written")
        return {"available": False, "reason": reason, "print_json": False}
    help_text = (probe.stdout or b"").decode("utf-8", "replace") + (probe.stderr or b"").decode("utf-8", "replace")
    return {"available": True, "reason": None, "print_json": "--print" in help_text}


def ledger_record(kind: str, payload: dict, env: Mapping[str, str] | None = None, *,
                  capability: dict | None = None) -> dict:
    """One OS ledger entry through `aces ledger record KIND --json -`, when the aces CLI offers it (`capability`, a
    ledger_capability() result, saves asking again)."""
    cap = capability if capability is not None else ledger_capability(env)
    if not cap["available"]:
        return {"written": False, "kind": kind, "reason": cap["reason"]}
    args = ["ledger", "record", kind, "--json", "-"]
    if cap.get("print_json"):
        args += ["--print", "json"]
    # backslashreplace: a lone surrogate goes out as the JSON escape \ud800 instead of failing after the bundle exists
    stdin_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8", "backslashreplace")
    done = run_aces(args, input_bytes=stdin_bytes, env=env)
    if done is None:
        return {"written": False, "kind": kind, "reason": NO_ACES}
    if done.returncode != 0:
        return {"written": False, "kind": kind,
                "reason": f"aces ledger record exited {done.returncode}: {_first_line(done.stderr)}"}
    # parse the whole first line: _first_line cuts at 300 characters, and the --print json line is about 245 plus the
    # ledger path, so a long path (any Windows path, a long ACES_STATE) would lose n, hash and path
    text = (done.stdout or b"").decode("utf-8", "replace").strip()
    try:
        data = json.loads(text.splitlines()[0]) if text else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        return {"written": True, "kind": kind, "via": "aces ledger record", "n": data.get("n"),
                "hash": data.get("hash"), "path": data.get("path")}
    return {"written": True, "kind": kind, "via": "aces ledger record", "output": _first_line(done.stdout)}


def add_cad_source(name: str, ref: str, env: Mapping[str, str] | None = None) -> dict:
    """Record a CAD source against the project through `aces project add-cad-source`, when the aces CLI offers it:
    {"recorded", "ref", "reason"?}."""
    if not aces_command(env):
        return {"recorded": False, "ref": ref, "reason": NO_ACES}
    probe = run_aces(["project", "add-cad-source", "--help"], env=env)
    if probe is None:
        return {"recorded": False, "ref": ref, "reason": NO_ACES}
    if probe.returncode != 0:
        return {"recorded": False, "ref": ref,
                "reason": "the installed aces CLI has no project add-cad-source command (aces-base 0.1.0); the "
                          "project's cad_sources list was not updated"}
    done = run_aces(["project", "add-cad-source", name, ref], env=env)
    if done is None:
        return {"recorded": False, "ref": ref, "reason": NO_ACES}
    if done.returncode != 0:
        return {"recorded": False, "ref": ref,
                "reason": f"aces project add-cad-source exited {done.returncode}: {_first_line(done.stderr)}"}
    return {"recorded": True, "ref": ref}
