"""The script contract on the server side: PARAMS and the entry point, read with `ast` (nothing is executed).

A script declares a module-level literal dict PARAMS (name -> a bare default, or a spec dict with default, type, min,
max, choices, unit, doc) and `def build(p)` (or the global cad skill's `def gen_step()` / `def gen_step(p)`). The
server reads PARAMS with ast.literal_eval, so the parameters are known and validated before any worker is spawned, and
one place declares the names. Overrides are JSON scalars checked against the declared type and range. The parameter
snapshot recorded everywhere is the full resolved dict, its sha256 over canonical JSON, the sorted overridden names
and the normalized declaration.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Mapping

from .errors import ParamsError, ScriptError, UsageError

SCRIPT_MAX_CHARS = 200_000
MAX_PARAMS = 64
NAME_RE = r"[a-z][a-z0-9_]{0,63}"
TYPES = ("bool", "int", "float", "str")
SPEC_KEYS = ("default", "type", "min", "max", "choices", "unit", "doc")
STR_MAX = 200
UNIT_MAX = 16
DOC_MAX = 200
CHOICES_MAX = 32
NOT_LITERAL = ("PARAMS must be a literal dict (numbers, strings, booleans, lists, dicts) so aces-cad can read it "
               "without running the script")
NO_ENTRY = "define def build(p) (or gen_step()) that returns the part"
# names a Mapping already uses: p.<name> gives the method, p["<name>"] the parameter
_MAPPING_ATTRS = ("get", "items", "keys", "values")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ScriptInfo:
    """What the server reads from a script without running it."""

    declared: dict
    entry: str
    entry_takes_p: bool
    warnings: list[str] = field(default_factory=list)


def canonical_json(obj) -> str:
    """sort_keys, (",", ":"), ensure_ascii=True, allow_nan=False."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def sha256_json(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("ascii")).hexdigest()


def _shown(value) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def check_script_text(text) -> str:
    """The script is UTF-8 text of 1-200,000 characters with no NUL and no lone surrogate (kind usage)."""
    if not isinstance(text, str):
        raise UsageError("the script must be text")
    if not 1 <= len(text) <= SCRIPT_MAX_CHARS:
        raise UsageError(f"the script must have 1 to {SCRIPT_MAX_CHARS} characters; it has {len(text)}")
    if "\x00" in text:
        raise UsageError("the script contains a NUL character")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise UsageError("the script contains a lone surrogate (it is not valid UTF-8 text)") from None
    return text


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value) -> bool:
    return not isinstance(value, float) or math.isfinite(value)


def _check_str(value, what: str) -> str:
    if len(value) > STR_MAX or _CONTROL_RE.search(value):
        raise ParamsError(f"{what} must be at most {STR_MAX} characters without control characters")
    return value


def _coerce(value, ptype: str, what: str):
    """`value` as a value of `ptype` (float accepts int, stored as float); ParamsError when it does not fit."""
    if ptype == "bool":
        if isinstance(value, bool):
            return value
    elif ptype == "int":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif ptype == "float":
        if _is_number(value):
            if not _finite(value):
                raise ParamsError(f"{what} must be a finite number; got {_shown(value)}")
            return float(value)
    elif ptype == "str":
        if isinstance(value, str):
            return _check_str(value, what)
    raise ParamsError(f"{what} must be a {ptype}; got {_shown(value)}")


def _infer(spec: dict) -> str:
    default = spec["default"]
    if isinstance(default, bool):
        return "bool"
    numbers = [spec[k] for k in ("default", "min", "max") if k in spec]
    if any(isinstance(v, float) for v in numbers):
        return "float"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in numbers):
        return "int"
    return "str"


def _normalize(name: str, value) -> dict:
    """One PARAMS entry as {type, default, min?, max?, choices?, unit?, doc?}."""
    what = f"PARAMS[{name!r}]"
    if isinstance(value, dict):
        unknown = sorted(str(k) for k in value if k not in SPEC_KEYS)
        if unknown:
            raise ParamsError(f"{what} has unknown keys {', '.join(unknown)}: use {', '.join(SPEC_KEYS)}")
        if "default" not in value:
            raise ParamsError(f"{what} needs a default")
        spec = dict(value)
    elif isinstance(value, (bool, int, float, str)):
        spec = {"default": value}
    else:
        raise ParamsError(f"{what} must be a default (bool, int, float, str) or a spec dict with a default; got "
                          f"{type(value).__name__}")
    ptype = spec.get("type")
    if ptype is None:
        ptype = _infer(spec)
    elif ptype not in TYPES:
        raise ParamsError(f"{what}['type'] must be one of {', '.join(TYPES)}; got {_shown(ptype)}")
    out = {"type": ptype, "default": _coerce(spec["default"], ptype, f"{what}['default']")}
    for bound in ("min", "max"):
        if bound in spec:
            limit = spec[bound]
            if ptype not in ("int", "float"):
                raise ParamsError(f"{what}['{bound}'] applies to int and float parameters only")
            if not _is_number(limit) or not _finite(limit):
                raise ParamsError(f"{what}['{bound}'] must be a finite number; got {_shown(limit)}")
            if ptype == "int" and not isinstance(limit, int):
                raise ParamsError(f"{what}['{bound}'] of an int parameter must be an integer; got {_shown(limit)}")
            out[bound] = float(limit) if ptype == "float" else limit
    if "min" in out and "max" in out and out["min"] > out["max"]:
        raise ParamsError(f"{what} has min {out['min']} above max {out['max']}")
    if "choices" in spec:
        choices = spec["choices"]
        if not isinstance(choices, list) or not 1 <= len(choices) <= CHOICES_MAX:
            raise ParamsError(f"{what}['choices'] must be a list of 1 to {CHOICES_MAX} values")
        out["choices"] = [_coerce(c, ptype, f"{what}['choices'][{i}]") for i, c in enumerate(choices)]
        for c in out["choices"]:
            _check_range(c, out, f"{what}['choices']")
    for key, cap in (("unit", UNIT_MAX), ("doc", DOC_MAX)):
        if key in spec:
            text = spec[key]
            if not isinstance(text, str) or len(text) > cap or _CONTROL_RE.search(text):
                raise ParamsError(f"{what}['{key}'] must be text of at most {cap} characters without control "
                                  f"characters")
            out[key] = text
    _check_range(out["default"], out, f"{what}['default']")
    if "choices" in out and out["default"] not in out["choices"]:
        raise ParamsError(f"{what}['default'] {_shown(out['default'])} is not one of its choices")
    return out


def _check_range(value, spec: dict, what: str) -> None:
    if spec["type"] in ("int", "float"):
        if "min" in spec and value < spec["min"]:
            raise ParamsError(f"{what} {_shown(value)} is below the minimum {spec['min']}")
        if "max" in spec and value > spec["max"]:
            raise ParamsError(f"{what} {_shown(value)} is above the maximum {spec['max']}")


def _binds_params(node: ast.AST) -> bool:
    """Whether a statement (not descending into functions or classes) binds the name PARAMS."""
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if getattr(n, "name", None) == "PARAMS":
                return True
            continue
        if isinstance(n, ast.Name) and n.id == "PARAMS" and isinstance(n.ctx, (ast.Store, ast.Del)):
            return True
        if isinstance(n, ast.alias) and (n.asname or n.name.split(".")[0]) == "PARAMS":
            return True
        if isinstance(n, (ast.Global, ast.Nonlocal)) and "PARAMS" in n.names:
            return True
        stack.extend(ast.iter_child_nodes(n))
    return False


def _params_node(tree: ast.Module):
    """The value node of the one top-level PARAMS assignment, or None when PARAMS is not declared."""
    simple = []
    other = 0
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PARAMS" for t in stmt.targets):
            simple.append(stmt)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == "PARAMS":
            simple.append(stmt)
        elif _binds_params(stmt):
            other += 1
    if len(simple) + other > 1 or (other and not simple):
        raise ParamsError("PARAMS must be assigned exactly once, at the top level of the script, as a literal dict")
    if not simple:
        return None
    stmt = simple[0]
    if isinstance(stmt, ast.Assign) and not all(isinstance(t, ast.Name) for t in stmt.targets):
        raise ParamsError(NOT_LITERAL)
    if stmt.value is None:
        raise ParamsError(NOT_LITERAL)
    return stmt.value


def _positional(fn: ast.FunctionDef) -> tuple[int, bool]:
    """(number of positional parameters, whether the signature is plain: no *args, keyword-only args defaulted)."""
    a = fn.args
    plain = a.vararg is None and all(d is not None for d in a.kw_defaults)
    return len(a.posonlyargs) + len(a.args), plain


def _entry(tree: ast.Module, warnings: list[str]) -> tuple[str, bool]:
    defs: dict[str, ast.FunctionDef] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name in ("build", "gen_step"):
            defs[stmt.name] = stmt  # the last definition wins, as at run time
    if "build" in defs:
        n, plain = _positional(defs["build"])
        if n != 1 or not plain:
            raise ScriptError("def build(p) must take exactly one positional parameter (the parameters p)",
                              line=defs["build"].lineno)
        if "gen_step" in defs:
            warnings.append("both build(p) and gen_step() are defined: build(p) is used")
        return "build", True
    if "gen_step" in defs:
        n, plain = _positional(defs["gen_step"])
        if n > 1 or not plain:
            raise ScriptError("def gen_step() must take no positional parameter, or one (the parameters p)",
                              line=defs["gen_step"].lineno)
        return "gen_step", n == 1
    raise ScriptError(NO_ENTRY)


def parse_script(text: str) -> ScriptInfo:
    """Read PARAMS and the entry point of a script without running it.

    usage: not text, empty, over 200,000 characters, a NUL or a lone surrogate; script: a SyntaxError (with line,
    offset, text) or no entry point; params: PARAMS that is not one literal dict of valid entries.
    """
    check_script_text(text)
    try:
        tree = ast.parse(text, filename="script.py")
    except SyntaxError as e:
        raise ScriptError(f"SyntaxError: {e.msg} (script.py, line {e.lineno})", line=e.lineno, offset=e.offset,
                          text=(e.text or "").rstrip("\n")[:400] or None, exception="SyntaxError") from None
    except (RecursionError, MemoryError, ValueError) as e:
        raise ScriptError(f"the script could not be parsed: {type(e).__name__}: {str(e)[:300]}",
                          exception=type(e).__name__) from None
    warnings: list[str] = []
    node = _params_node(tree)
    declared: dict = {}
    if node is not None:
        try:
            raw = ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            raise ParamsError(NOT_LITERAL) from None
        if not isinstance(raw, dict):
            raise ParamsError(NOT_LITERAL)
        if len(raw) > MAX_PARAMS:
            raise ParamsError(f"PARAMS declares {len(raw)} names; at most {MAX_PARAMS} are allowed")
        for name, value in raw.items():
            if not isinstance(name, str) or not re.fullmatch(NAME_RE, name):
                raise ParamsError(f"PARAMS name {_shown(name)} must match {NAME_RE} (lower case, digits and _, "
                                  f"starting with a letter)")
            declared[name] = _normalize(name, value)
            if name in _MAPPING_ATTRS:
                warnings.append(f"parameter {name!r} shadows a method of p: read it as p[{name!r}]")
    entry, takes_p = _entry(tree, warnings)
    return ScriptInfo(declared=declared, entry=entry, entry_takes_p=takes_p, warnings=warnings)


def defaults(declared: Mapping) -> dict:
    return {name: spec["default"] for name, spec in declared.items()}


def resolve(declared: dict, overrides: Mapping) -> dict:
    """The full value dict: every declared default, with the overrides applied; ParamsError on a bad override."""
    overrides = dict(overrides or {})
    if overrides and not declared:
        names = ", ".join(sorted(map(str, overrides)))
        raise ParamsError(f"the script declares no PARAMS, so it takes no parameters; got {names}")
    values = defaults(declared)
    for name, value in overrides.items():
        if name not in declared:
            raise ParamsError(f"unknown parameter {_shown(name)}: the script declares {', '.join(sorted(declared))}")
        spec = declared[name]
        if not isinstance(value, (bool, int, float, str)):
            raise ParamsError(f"parameter {name} must be a JSON scalar (number, boolean or string); got "
                              f"{type(value).__name__}")
        coerced = _coerce(value, spec["type"], f"parameter {name}")
        _check_range(coerced, spec, f"parameter {name}")
        if "choices" in spec and coerced not in spec["choices"]:
            raise ParamsError(f"parameter {name} must be one of {', '.join(map(_shown, spec['choices']))}; got "
                              f"{_shown(coerced)}")
        values[name] = coerced
    return values


def snapshot(info: ScriptInfo, overrides: Mapping) -> dict:
    """{"params", "params_overridden", "params_declared", "params_sha256"} for a run record."""
    values = resolve(info.declared, overrides)
    return {"params": values, "params_overridden": sorted(dict(overrides or {})),
            "params_declared": info.declared, "params_sha256": sha256_json(values)}
