"""FeatureScript helpers: headers, imports, enums, value decoding, feature summaries, custom-feature parameters and
namespaces. Pure functions; nothing here talks to Onshape.

Namespaces. A custom feature instance (BTMFeature-134) names the Feature Studio that defines it in `namespace`
("Features in the FeatureScript standard library have a namespace value of ''"). Verified from the OpenAPI
description: the two string forms e<eid>::m<mid> (Feature Studio and Part Studio in the same workspace) and
d<did>::v<vid>::e<eid>::m<mid> (different workspaces), where <mid> is the Feature Studio element's microversionId from
getElementsInDocument; both are documented for the /fstable tableNamespace query. NOT verified without an Onshape
account:

- that POST .../features accepts these forms for a feature (they are documented for custom tables);
- that the d::v::e::m form is accepted for a version of the same document;
- whether an instance follows later writes to its studio or has to be re-pointed (hence replace_feature_id);
- whether featurespecs[].namespace, from the Feature Studio or from the Part Studio, already holds the string to use;
- which namespace a BTMParameterEnum-145 needs: the feature's for an enum the studio declares with `export enum`,
  Onshape's reported value (often "") for a standard-library enum the studio re-exports or for an imported studio's.
"""
from __future__ import annotations

import copy
import json
import re

from .errors import UsageError

_COMMENT_RE = re.compile(r"/\*.*?(?:\*/|\Z)|//[^\n]*", re.S)
# A block comment ends at its first */ ((?:[^*]|\*(?!/))* cannot cross one), so text that starts with many comments
# has exactly one parse and the match is linear; a lazy /\*.*?\*/ could also end at any later */, which made header-less
# text with N leading comments take 2^(N-1) tries and read code after a */ as comment.
HEADER_RE = re.compile(r"\A(?:\s|//[^\n]*(?:\n|\Z)|/\*(?:[^*]|\*(?!/))*\*/)*FeatureScript\s+(\d+)\s*;", re.S)
STD_IMPORT_RE = re.compile(
    r'^[ \t]*(?:export[ \t]+)?import[ \t]*\([ \t]*path[ \t]*:[ \t]*"(onshape/std/[^"\n]*\.fs)"[ \t]*,[ \t]*'
    r'version[ \t]*:[ \t]*"([^"\n]*)"[ \t]*\)[ \t]*;', re.M)
# The alias is tried only where an identifier starts (\b), so a long identifier-like run is scanned once, not once
# per position (which was quadratic in its length).
IMPORT_RE = re.compile(
    r'(?:\b(export)\s+)?(?:\b([A-Za-z_][A-Za-z0-9_]*)\s*::\s*)?\bimport\s*\(\s*path\s*:\s*"([^"]*)"\s*,\s*version\s*:'
    r'\s*"([^"]*)"\s*\)\s*;')
ENUM_RE = re.compile(r"\bexport\s+enum\s+([A-Za-z_][A-Za-z0-9_]*)")
NAMESPACE_RE = r"(?:d([0-9a-f]{24})::v([0-9a-f]{24})::)?e([0-9a-f]{24})::m([0-9a-f]{24})"
FEATURE_ID_RE = r"[A-Za-z0-9_]{1,64}"

QUANTITY_SPECS = ("BTParameterSpecQuantity-173", "BTParameterSpecNullableQuantity-715")
DEFAULT_KIND = {
    "BTMParameterQuantity-147": "quantity",
    "BTMParameterString-149": "string",
    "BTMParameterBoolean-144": "boolean",
    "BTMParameterEnum-145": "enum",
    "BTMParameterQueryList-148": "query",
}
SPEC_KIND = {
    "BTParameterSpecQuantity-173": "quantity",
    "BTParameterSpecNullableQuantity-715": "quantity",
    "BTParameterSpecString-175": "string",
    "BTParameterSpecBoolean-170": "boolean",
    "BTParameterSpecEnum-171": "enum",
    "BTParameterSpecQuery-174": "query",
}


def strip_comments(src: str) -> str:
    """Remove // and /* */ comments (string literals are not special-cased; fine for imports and enum names)."""
    return _COMMENT_RE.sub(lambda m: " " + "\n" * m.group(0).count("\n"), src)


# --- 7.1 headers --------------------------------------------------------------------------------------------------

def parse_header(contents: str) -> dict:
    m = HEADER_RE.match(contents or "")
    return {
        "language_version": int(m.group(1)) if m else None,
        "std_imports": [{"path": i.group(1), "version": i.group(2)} for i in STD_IMPORT_RE.finditer(contents or "")],
    }


def header_lines(contents: str) -> list[str]:
    """The `FeatureScript N;` line and every std import line, verbatim and in order."""
    contents = contents or ""
    lines = contents.splitlines()
    starts = []
    pos = 0
    for raw in contents.splitlines(keepends=True):  # the same split, with the real line-ending lengths
        starts.append(pos)
        pos += len(raw)
    wanted: set[int] = set()

    def line_of(offset: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo

    m = HEADER_RE.match(contents)
    if m and lines:
        wanted.add(line_of(m.start(1)))
    for i, line in enumerate(lines):
        if STD_IMPORT_RE.match(line):
            wanted.add(i)
    return [lines[i] for i in sorted(wanted)]


def ensure_header(code: str, current_contents: str) -> tuple[str, bool, list[str]]:
    """Return (code to write, whether the studio's header was prepended, warnings)."""
    warnings: list[str] = []
    own = HEADER_RE.match(code)
    if own:
        previous = parse_header(current_contents)["language_version"]
        version = int(own.group(1))
        if previous is not None and previous != version:
            warnings.append(f"code declares FeatureScript {version}; the studio's header declared {previous}")
        if not parse_header(code)["std_imports"]:
            warnings.append("no import of onshape/std/...: standard library functions will not resolve")
        return code, False, warnings
    if not HEADER_RE.match(current_contents or ""):
        warnings.append("the studio has no FeatureScript header to copy; the code was written as given")
        return code, False, warnings
    return "\n".join(header_lines(current_contents)) + "\n\n" + code, True, warnings


# --- 7.2 values and notices ---------------------------------------------------------------------------------------

def decode_fsvalue(v):
    """A BTFSValue-1888 as plain JSON values."""
    if not isinstance(v, dict):
        return v
    bt = v.get("btType")
    if bt == "BTFSValueNumber-772":
        return v.get("value")
    if bt == "BTFSValueWithUnits-1817":
        return {"value": v.get("value"), "units": v.get("unitToPower")}
    if bt in ("BTFSValueString-1422", "BTFSValueBoolean-1195"):
        return v.get("value")
    if bt == "BTFSValueArray-1499":
        return [decode_fsvalue(x) for x in v.get("value") or []]
    if bt == "BTFSValueMap-2062":
        out: dict = {}
        for entry in v.get("value") or []:
            if not isinstance(entry, dict):
                continue
            key = decode_fsvalue(entry.get("key"))
            if not isinstance(key, str):
                key = json.dumps(key, ensure_ascii=False, sort_keys=True)
            out[key] = decode_fsvalue(entry.get("value"))
        if v.get("typeTag"):
            out["_typeTag"] = v["typeTag"]
        return out
    if bt == "BTFSValueUndefined-2003":
        return None
    if bt == "BTFSValueTooBig-1247":
        return {"too_big": True}
    if bt == "BTFSValueOther-1124":
        return {"other": v.get("type"), "typeTag": v.get("typeTag")}
    return v


def compact_notice(n: dict) -> dict:
    out = {"level": n.get("level"), "type": n.get("type"), "message": n.get("message")}
    if n.get("parameterId"):
        out["parameter_id"] = n["parameterId"]
    infos = n.get("locationInfos")
    if isinstance(infos, list) and infos:
        out["location"] = infos[0]
    return out


# --- 7.3 feature summaries ----------------------------------------------------------------------------------------

def compact_parameter(p: dict) -> dict:
    out = {"id": p.get("parameterId"), "bt_type": p.get("btType")}
    bt = p.get("btType")
    if bt == "BTMParameterQuantity-147":
        for src, dst in (("expression", "expression"), ("value", "value"), ("units", "units"),
                         ("isInteger", "is_integer")):
            if src in p:
                out[dst] = p[src]
    elif bt in ("BTMParameterString-149", "BTMParameterBoolean-144"):
        if "value" in p:
            out["value"] = p["value"]
    elif bt == "BTMParameterEnum-145":
        out["enum_name"] = p.get("enumName")
        out["value"] = p.get("value")
        out["namespace"] = p.get("namespace")
    elif bt == "BTMParameterQueryList-148":
        out["queries"] = [list(q.get("deterministicIds") or []) for q in p.get("queries") or []
                          if isinstance(q, dict)]
    return out


def compact_feature(f: dict, states) -> dict:
    fid = f.get("featureId")
    state = states.get(fid) if isinstance(states, dict) else None
    return {
        "feature_id": fid,
        "name": f.get("name"),
        "feature_type": f.get("featureType"),
        "namespace": f.get("namespace"),
        "bt_type": f.get("btType"),
        "suppressed": f.get("suppressed"),
        "status": state.get("featureStatus") if isinstance(state, dict) else None,
        "parameters": [compact_parameter(p) for p in f.get("parameters") or [] if isinstance(p, dict)],
    }


# --- 7.4 custom-feature parameters --------------------------------------------------------------------------------

def find_spec(specs: list, feature_type: str) -> dict:
    """The BTFeatureSpec-129 whose featureType (or else featureTypeName) is `feature_type`."""
    specs = [s for s in specs or [] if isinstance(s, dict)]
    for s in specs:
        if s.get("featureType") == feature_type:
            return s
    for s in specs:
        if s.get("featureTypeName") == feature_type:
            return s
    available = ", ".join(f"{s.get('featureType')} ({s.get('featureTypeName')})" for s in specs) or "none"
    raise UsageError(f"no feature type {feature_type!r} in this Feature Studio; available: {available}")


def enum_namespace(spec_param: dict, feature_namespace: str, studio_enums) -> str:
    """The feature's namespace for an enum the studio itself exports, else the namespace Onshape reported."""
    if spec_param.get("enumName") in studio_enums:
        return feature_namespace
    return spec_param.get("namespace") or ""


def _param_specs(spec: dict) -> list[dict]:
    return [p for p in (spec.get("parameters") or spec.get("allParameters") or []) if isinstance(p, dict)]


def _kind(ps: dict) -> str | None:
    kind = SPEC_KIND.get(ps.get("btType") or "")
    if kind is None:
        default = ps.get("defaultValue")
        if isinstance(default, dict):
            kind = DEFAULT_KIND.get(default.get("btType") or "")
    return kind


def _build_one(ps: dict, pid: str, v, feature_namespace: str, studio_enums) -> dict:
    kind = _kind(ps)
    if kind == "quantity":
        qtype = ps.get("quantityType") or "UNKNOWN"
        if isinstance(v, str):
            return {"btType": "BTMParameterQuantity-147", "parameterId": pid, "expression": v,
                    "isInteger": qtype == "INTEGER"}
        if isinstance(v, (int, float)) and not isinstance(v, bool) and qtype in ("INTEGER", "REAL"):
            if qtype == "INTEGER" and isinstance(v, float) and not v.is_integer():
                raise UsageError(f"parameter {pid} is an INTEGER quantity: give a whole number")
            if qtype == "INTEGER":
                v = int(v)
            return {"btType": "BTMParameterQuantity-147", "parameterId": pid, "expression": str(v),
                    "isInteger": qtype == "INTEGER"}
        raise UsageError(f'parameter {pid} is a {qtype} quantity: give an expression with units, e.g. "20 mm"')
    if kind == "string":
        if not isinstance(v, str):
            raise UsageError(f"parameter {pid} is a string parameter: give a string")
        return {"btType": "BTMParameterString-149", "parameterId": pid, "value": v}
    if kind == "boolean":
        if not isinstance(v, bool):
            raise UsageError(f"parameter {pid} is a boolean parameter: give true or false")
        return {"btType": "BTMParameterBoolean-144", "parameterId": pid, "value": v}
    if kind == "enum":
        if not isinstance(v, str):
            raise UsageError(f"parameter {pid} is an enum parameter: give the value name as a string")
        options = ps.get("options") or []
        if options and v not in options:
            raise UsageError(f"parameter {pid} must be one of {', '.join(map(str, options))}; got {v!r}")
        enum_name = ps.get("enumName")
        if enum_name is None and isinstance(ps.get("defaultValue"), dict):
            enum_name = ps["defaultValue"].get("enumName")
        return {"btType": "BTMParameterEnum-145", "parameterId": pid, "value": v, "enumName": enum_name,
                "namespace": enum_namespace(ps, feature_namespace, studio_enums)}
    if kind == "query":
        ids = [v] if isinstance(v, str) else v
        if not isinstance(ids, list) or not ids or not all(isinstance(x, str) and x for x in ids):
            raise UsageError(f"parameter {pid} is a query parameter: give a deterministic id or a list of them")
        return {"btType": "BTMParameterQueryList-148", "parameterId": pid,
                "queries": [{"btType": "BTMIndividualQuery-138", "deterministicIds": list(ids)}]}
    raise UsageError(f"parameter {pid} ({ps.get('btType')}) needs a raw BTMParameter map with btType")


def build_parameters(spec: dict, values: dict | None, *, feature_namespace: str = "",
                     studio_enums=frozenset()) -> list[dict]:
    """BTMParameter maps for a custom feature, in the spec's order; defaults for parameters not given."""
    pspecs = _param_specs(spec)
    values = dict(values or {})
    valid = [p.get("parameterId") for p in pspecs]
    unknown = sorted(str(k) for k in values if k not in valid)
    if unknown:
        raise UsageError(f"unknown parameter(s) {', '.join(unknown)} for feature type {spec.get('featureType')}; "
                         f"valid parameter ids: {', '.join(str(v) for v in valid) or 'none'}")
    out = []
    for ps in pspecs:
        pid = ps.get("parameterId")
        if pid in values:
            v = values[pid]
            if isinstance(v, dict) and "btType" in v:
                p = copy.deepcopy(v)
                p["parameterId"] = pid
                out.append(p)
            else:
                out.append(_build_one(ps, pid, v, feature_namespace, studio_enums))
            continue
        default = ps.get("defaultValue")
        if isinstance(default, dict) and default:
            p = copy.deepcopy(default)
            p["parameterId"] = pid
            if p.get("btType") == "BTMParameterEnum-145":
                p["namespace"] = enum_namespace(ps, feature_namespace, studio_enums)
            out.append(p)
    return out


# --- 7.5 namespaces -----------------------------------------------------------------------------------------------

def workspace_namespace(studio_eid: str, element_microversion: str) -> str:
    return f"e{studio_eid}::m{element_microversion}"


def version_namespace(did: str, vid: str, studio_eid: str, element_microversion: str) -> str:
    return f"d{did}::v{vid}::e{studio_eid}::m{element_microversion}"


def parse_namespace(ns) -> dict | None:
    if not isinstance(ns, str):
        return None
    m = re.fullmatch(NAMESPACE_RE, ns)
    if not m:
        return None
    return {"document_id": m.group(1), "version_id": m.group(2), "element_id": m.group(3),
            "microversion": m.group(4)}


# --- 7.6 the feature messages probe -------------------------------------------------------------------------------

def feature_messages_script(feature_id: str) -> str:
    """An eval lambda returning the feature's error, warning and info text (top-level feature id; unverified)."""
    if not isinstance(feature_id, str) or not re.fullmatch(FEATURE_ID_RE, feature_id):
        raise UsageError(f"feature id must be letters, digits and _ (at most 64); got {feature_id!r}")
    return (
        "function(context is Context, queries is map)\n"
        "{\n"
        f'    const id = makeId("{feature_id}");\n'
        '    return { "error" : getFeatureError(context, id), "warning" : getFeatureWarning(context, id), '
        '"info" : getFeatureInfo(context, id) };\n'
        "}\n"
    )


# --- 7.7 imports, 7.8 enums ---------------------------------------------------------------------------------------

def parse_imports(contents: str) -> list[dict]:
    """Every import statement outside comments, classified as std, workspace, linked or unrecognised."""
    out = []
    for m in IMPORT_RE.finditer(strip_comments(contents or "")):
        exported, alias, path, version = m.groups()
        item = {"path": path, "version": version, "kind": "unrecognised", "alias": alias,
                "exported": exported is not None}
        if re.fullmatch(r"onshape/std/.+", path):
            item["kind"] = "std"
        elif re.fullmatch(r"[0-9a-fA-F]{24}", path):
            item["kind"] = "workspace"
            item["element_id"] = path.lower()
        elif re.fullmatch(r"[0-9a-fA-F]{24}/[0-9a-fA-F]{24}/[0-9a-fA-F]{24}", path):
            did, vid, eid = path.lower().split("/")
            item.update({"kind": "linked", "document_id": did, "version_id": vid, "element_id": eid})
        out.append(item)
    return out


def declared_enums(contents: str) -> set[str]:
    return set(ENUM_RE.findall(strip_comments(contents or "")))
