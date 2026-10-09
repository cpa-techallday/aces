"""The `aces` command (aces-base) as a subprocess: projects, evidence references and the operator ledger.

The golden path runs in the aces-engineering venv, which cannot import aces-base (a system package for the system
Python), so it talks to `aces` the way an operator does. The command is ACES_CMD when set (a JSON array of strings,
e.g. ["/path/python", "-m", "aces.cli"], or one path), else `aces` on PATH. Children run with UTF-8 stdio so JSON
with non-ASCII text survives on every platform.

Standard library only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class AcesCliError(RuntimeError):
    """`aces` could not be run, or a command failed; the message is the command's own error line when it gave one."""


def _command_from_env() -> tuple[list[str] | None, str | None]:
    raw = os.environ.get("ACES_CMD", "")
    if raw.strip():
        if raw.lstrip().startswith("["):
            try:
                cmd = json.loads(raw)
            except ValueError as e:
                return None, f"ACES_CMD is not a JSON array of strings ({e})"
            if not (isinstance(cmd, list) and cmd and all(isinstance(c, str) and c for c in cmd)):
                return None, "ACES_CMD must be a non-empty JSON array of strings"
            return cmd, None
        return [raw], None
    found = shutil.which("aces")
    if found:
        return [found], None
    return None, "no `aces` on PATH (install aces-base, or set ACES_CMD)"


class AcesCli:
    """Thin wrapper over `aces`. Methods raise AcesCliError on failure unless documented otherwise."""

    def __init__(self, argv0: list[str] | None = None, *, timeout_s: float = 60.0):
        if argv0 is None:
            self.argv0, self.problem = _command_from_env()
        else:
            self.argv0, self.problem = list(argv0), None
        self.timeout_s = float(timeout_s)
        self._available: bool | None = None

    def __repr__(self) -> str:
        return f"AcesCli({self.argv0!r})"

    # ---- plumbing -----------------------------------------------------------------------------------------------
    def _run(self, args: list[str], *, timeout_s: float | None = None) -> subprocess.CompletedProcess:
        if not self.argv0:
            raise AcesCliError(self.problem or "aces is not available")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            return subprocess.run(self.argv0 + list(args), capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", stdin=subprocess.DEVNULL, env=env,
                                  timeout=timeout_s or self.timeout_s, check=False)
        except FileNotFoundError as e:
            raise AcesCliError(f"cannot run {self.argv0[0]}: not found") from e
        except subprocess.TimeoutExpired as e:
            raise AcesCliError(f"`aces {' '.join(args)}` did not finish within {timeout_s or self.timeout_s:.0f} s") from e
        except OSError as e:
            raise AcesCliError(f"cannot run {self.argv0[0]}: {e}") from e

    def _check(self, args: list[str]) -> subprocess.CompletedProcess:
        cp = self._run(args)
        if cp.returncode != 0:
            raise AcesCliError(_first_error(cp) or f"`aces {' '.join(args)}` exited {cp.returncode}")
        return cp

    def _json(self, args: list[str]) -> Any:
        cp = self._check(args)
        try:
            return json.loads(cp.stdout)
        except ValueError as e:
            raise AcesCliError(f"`aces {' '.join(args)}` did not print JSON ({e}): {cp.stdout[:200]!r}") from e

    # ---- probes -------------------------------------------------------------------------------------------------
    def available(self) -> bool:
        """`aces --version` runs and exits 0 (cached); `problem` says why not."""
        if self._available is None:
            if not self.argv0:
                self._available = False
            else:
                try:
                    cp = self._run(["--version"])
                    self._available = cp.returncode == 0
                    if not self._available:
                        self.problem = _first_error(cp) or f"`aces --version` exited {cp.returncode}"
                except AcesCliError as e:
                    self._available, self.problem = False, str(e)
        return self._available

    def supports_record(self) -> bool:
        """`aces ledger record --help` exits 0 (aces-base 0.1.0-3 or later)."""
        try:
            return self._run(["ledger", "record", "--help"]).returncode == 0
        except AcesCliError:
            return False

    def version(self) -> str | None:
        """The version `aces --version` prints (`aces 0.1.0` -> "0.1.0"), or None."""
        try:
            cp = self._run(["--version"])
        except AcesCliError:
            return None
        if cp.returncode != 0:
            return None
        words = (cp.stdout or cp.stderr).split()
        return words[-1] if words else None

    # ---- projects -----------------------------------------------------------------------------------------------
    def project_show(self, name: str) -> dict | None:
        """`aces project show NAME --json`, or None when there is no such project."""
        cp = self._run(["project", "show", name, "--json"])
        if cp.returncode != 0:
            err = _first_error(cp)
            if "no project" in (err or ""):
                return None
            raise AcesCliError(err or f"`aces project show {name}` exited {cp.returncode}")
        try:
            data = json.loads(cp.stdout)
        except ValueError as e:
            raise AcesCliError(f"`aces project show {name} --json` did not print JSON ({e})") from e
        return data if isinstance(data, dict) else None

    def project_new(self, name: str, *, cls: str | None = None, hypothesis: str | None = None) -> None:
        args = ["project", "new", name]
        if cls:
            args += ["--class", cls]
        if hypothesis:
            args += ["--hypothesis", hypothesis]
        self._check(args)

    def add_component(self, name: str, component: str) -> None:
        self._check(["project", "add-component", name, component])

    def add_evidence(self, name: str, ref: str) -> None:
        self._check(["project", "add-evidence", name, ref])

    def add_cad_source(self, name: str, ref: str) -> None:
        self._check(["project", "add-cad-source", name, ref])

    # ---- ledger -------------------------------------------------------------------------------------------------
    def ledger_record(self, kind: str, payload_path: "Path | str") -> dict:
        """`aces ledger record KIND --json FILE --print json` -> {"recorded", "n", "kind", "ts", "hash", "prev",
        "path"}."""
        out = self._json(["ledger", "record", kind, "--json", str(payload_path), "--print", "json"])
        if not isinstance(out, dict) or not out.get("recorded"):
            raise AcesCliError(f"`aces ledger record {kind}` did not confirm the entry: {out!r}")
        return out

    def ledger_verify(self) -> dict:
        """`aces ledger verify --json` -> {"ok", "entries", "message", "path"}; a broken chain is ok False, not an
        exception."""
        cp = self._run(["ledger", "verify", "--json"])
        try:
            data = json.loads(cp.stdout)
        except ValueError:
            raise AcesCliError(_first_error(cp) or f"`aces ledger verify --json` exited {cp.returncode} without JSON") from None
        if not isinstance(data, dict):
            raise AcesCliError(f"`aces ledger verify --json` printed {data!r}")
        return data

    def ledger_find(self, kind: str) -> list[dict]:
        """Every entry of `kind` (`aces ledger show -n 0 --kind KIND --json`), each with its 1-based position n."""
        out = self._json(["ledger", "show", "-n", "0", "--kind", kind, "--json"])
        if not isinstance(out, list):
            raise AcesCliError(f"`aces ledger show --json` printed {type(out).__name__}, not a list")
        return out


def _first_error(cp: subprocess.CompletedProcess) -> str:
    """The error `aces` printed: its first `aces: <message>` line without the prefix, else the last stderr line (an
    argparse error, or the exception line of a traceback)."""
    lines = [ln.strip() for ln in (cp.stderr or "").splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("aces: "):
            return ln[6:]
    return lines[-1] if lines else ""
