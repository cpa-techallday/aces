"""The class gate, the bound project and the bridge to the aces CLI.

Onshape is a cloud service. VISION.md section 5 step 2 allows it for personal, open and commercial projects and never
for sovereign or controlled ones, so every tool that sends caller content to Onshape, changes a document or writes
local files names an ACES project and is refused unless that project's class is on the allowlist (an unknown or
missing class is refused too). The class comes from `aces project show --json NAME` when the aces CLI is available,
else from <ACES_WORK>/<name>/aces.yaml read the way aces-base reads it.

ACES_ONSHAPE_PROJECT binds the CLI and the MCP server to one project: a different project is refused, and even the
read-only tools run the gate for the bound project. The aces CLI is ACES_ONSHAPE_ACES_CMD (a JSON array of strings,
or a single path; [] means none) or `aces` on PATH. It is probed for `ledger record` and `project add-cad-source`,
which aces-base 0.1.0 does not have, and the results say plainly when nothing was written.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Mapping

from . import config
from .errors import ClassRefused, ConfigError, ProjectError

ALLOWED_CLASSES = ("personal", "open", "commercial")
PROJECT_NAME_RE = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
NO_ACES = "no aces CLI on PATH (or ACES_ONSHAPE_ACES_CMD=[])"
KEY_VARS = ("ONSHAPE_ACCESS_KEY", "ONSHAPE_SECRET_KEY")


def _environ(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def aces_command(env: Mapping[str, str] | None = None) -> list[str] | None:
    """The aces CLI as an argv prefix, or None when there is none."""
    raw = _environ(env).get("ACES_ONSHAPE_ACES_CMD")
    if raw is None or not raw.strip():
        found = shutil.which("aces")
        return [found] if found else None
    raw = raw.strip()
    bad = ConfigError('ACES_ONSHAPE_ACES_CMD must be a JSON array of strings (for example ["/usr/bin/aces"]) or a '
                      'single path; [] means no aces CLI')
    if raw.startswith("["):
        try:
            argv = json.loads(raw)
        except ValueError:
            raise bad from None
        if not isinstance(argv, list) or not all(isinstance(a, str) and a for a in argv):
            raise bad
        return argv or None
    if raw.startswith(("{", '"')):
        raise bad
    return [raw]


def run_aces(args: list[str], *, input_bytes: bytes | None = None, env: Mapping[str, str] | None = None,
             timeout: float = 30):
    """Run the aces CLI with the Onshape keys removed from its environment; None when there is no aces CLI.

    ACES_WORK is set to the adapter's own project root (config.work_dir), so `aces project show`, `ledger record` and
    `project add-cad-source` look for projects where the adapter writes: aces-base reads an ACES_WORK that is set but
    empty as the current directory, the adapter as unset (~/Work), and the class must come from the project whose
    directory receives the files.
    """
    cmd = aces_command(env)
    if not cmd:
        return None
    child = {k: v for k, v in _environ(env).items() if k not in KEY_VARS}
    child["ACES_WORK"] = str(config.work_dir(env))
    try:
        return subprocess.run(cmd + list(args), input=input_bytes, capture_output=True, timeout=timeout, env=child)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd + list(args), 124, b"", f"timed out after {timeout} s".encode())
    except (FileNotFoundError, OSError):
        return None


def _first_line(data: bytes | None) -> str:
    text = (data or b"").decode("utf-8", "replace").strip()
    return text.splitlines()[0].strip() if text else "(no output)"


def validate_project_name(name) -> str:
    if not isinstance(name, str) or not re.fullmatch(PROJECT_NAME_RE, name):
        raise ProjectError(f"invalid project name {name!r}: use letters, digits, '.', '_' and '-' (at most 64 "
                           f"characters, starting with a letter or digit)")
    return name


def bound_project(env: Mapping[str, str] | None = None) -> str | None:
    value = (_environ(env).get("ACES_ONSHAPE_PROJECT") or "").strip()
    return validate_project_name(value) if value else None


def resolve_project(name, env: Mapping[str, str] | None = None) -> str:
    """The project a call is for: `name`, or the bound project; never a project other than the bound one."""
    bound = bound_project(env)
    if bound and name and name != bound:
        raise ProjectError(f"this aces-onshape is bound to project '{bound}' (ACES_ONSHAPE_PROJECT); refusing "
                           f"project '{name}'")
    result = name or bound
    if not result:
        raise ProjectError("no ACES project given: pass --project NAME (MCP: the project argument) or set "
                           "ACES_ONSHAPE_PROJECT; Onshape writes, evaluation, exports and pulls run the class gate "
                           "for a project")
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
                       f"--class <personal|open|commercial>")


def check_cloud_allowed(name, env: Mapping[str, str] | None = None) -> dict:
    """The class gate. Runs before any client is built or any request is sent."""
    info = project_class(name, env)
    cls = info["class"]
    if cls not in ALLOWED_CLASSES:
        raise ClassRefused(f"project '{info['name']}' is class '{cls or 'none'}': Onshape is a cloud service, allowed "
                           f"only for personal, open and commercial projects and never for sovereign or controlled "
                           f"ones (VISION.md section 5, step 2); use build123d or the voxel kernel for this project")
    return info


def gate_from_env(env: Mapping[str, str] | None = None) -> dict | None:
    """The gate for the bound project, when there is one (the read-only tools run this)."""
    bound = bound_project(env)
    return check_cloud_allowed(bound, env) if bound else None


def ledger_capability(env: Mapping[str, str] | None = None) -> dict:
    """Whether the aces CLI offers `ledger record`, from `aces ledger record --help`: {"available", "reason",
    "print_json"}. The pull asks before it writes source.json, so source.json itself says when the OS ledger entry is
    not written."""
    if not aces_command(env):
        return {"available": False, "reason": NO_ACES, "print_json": False}
    probe = run_aces(["ledger", "record", "--help"], env=env)
    if probe is None:
        return {"available": False, "reason": NO_ACES, "print_json": False}
    if probe.returncode != 0:
        if probe.returncode == 124 and (probe.stderr or b"").startswith(b"timed out"):  # run_aces's own timeout
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
    if cap["print_json"]:
        args += ["--print", "json"]
    # backslashreplace: a lone surrogate from Onshape text goes out as the JSON escape \ud800 instead of failing here,
    # after the bundle is already in place
    stdin_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8", "backslashreplace")
    done = run_aces(args, input_bytes=stdin_bytes, env=env)
    if done is None:
        return {"written": False, "kind": kind, "reason": NO_ACES}
    if done.returncode != 0:
        return {"written": False, "kind": kind,
                "reason": f"aces ledger record exited {done.returncode}: {_first_line(done.stderr)}"}
    first = _first_line(done.stdout)
    try:
        data = json.loads(first)
    except ValueError:
        data = None
    if isinstance(data, dict):
        return {"written": True, "kind": kind, "via": "aces ledger record", "n": data.get("n"),
                "hash": data.get("hash"), "path": data.get("path")}
    return {"written": True, "kind": kind, "via": "aces ledger record", "output": first}


def add_cad_source(name: str, ref: str, env: Mapping[str, str] | None = None) -> dict:
    """Record a CAD source against the project through `aces project add-cad-source`, when the aces CLI offers it."""
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
