"""Operations: what the CLI verbs and the MCP tools do, on top of the REST client.

Dispatch order for every call (section 9.3 of the spec), so that a failure at any step sends no request at all:

1. validate the arguments without network (ids, names, labels, JSON, the syntax of a reference);
2. the class gate: tools that send caller content to Onshape, change a document or write local files resolve their
   ACES project and refuse sovereign and controlled ones; the read-only tools run the gate for the bound project;
3. build the client (settings only, no request);
4. run the operation. Every operation that takes `project` runs the class gate again as its first statement, so a
   library caller gets it too, then its own no-network checks (a foreign target, the host of a reference), then its
   requests.

Ownership. A Session records the documents, Feature Studios and features this process created. Rewriting or deleting
anything else (a studio, a feature, or a feature replaced through add_custom_feature) needs confirm_foreign, and is
refused before any request without it. The MCP server keeps one Session for its life; the CLI keeps none, so every
existing target is foreign to it. Writing into a public document (BTDocumentInfo.public; a free Onshape plan makes
every document public) needs allow_public.

Text read from Onshape (FeatureScript source, feature, element and document names, error messages) is data: it is
recorded and returned, never interpreted.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

from . import __version__, config
from . import project as aces_project
from .client import (
    STL_UNITS,
    Client,
    utc_z,
    validate_feature_id,
    validate_id,
    validate_wvm,
    validate_xid,
)
from .errors import AdapterError, AuthError, OnshapeError, UsageError, WriteRefused
from .featurescript import (
    build_parameters,
    compact_feature,
    compact_notice,
    compact_parameter,
    declared_enums,
    decode_fsvalue,
    ensure_header,
    feature_messages_script,
    find_spec,
    parse_header,
    parse_imports,
    parse_namespace,
    version_namespace,
    workspace_namespace,
)

# The 9.1 id validators live in client.py (the client validates before every call) and are re-exported here with
# validate_label, validate_name, validate_feature_type, parse_ref and check_ref_host.
VALIDATORS = (validate_id, validate_wvm, validate_feature_id, validate_xid)

TOOL = {"name": "aces-cad-onshape", "version": __version__}
IMPORT_DEPTH = 3
STUDIO_LIMIT = 20
ELEMENT_TYPES = ("PARTSTUDIO", "ASSEMBLY", "DRAWING", "FEATURESTUDIO", "BLOB", "APPLICATION", "TABLE",
                 "BILLOFMATERIALS", "VARIABLESTUDIO", "PUBLICATIONITEM")
STEP_NEEDS_WV = "STEP export needs a workspace or a version (the translation endpoint takes w or v)"
DEFAULT_STUDIO_NAME = "ACES FeatureScript"
LABEL_RE = r"[A-Za-z0-9._-]{1,40}"
NAME_RE = r"[^\x00-\x1f\x7f]{1,256}"
REF_RAW_RE = r"[\x21-\x7e]{1,512}"
REF_FORMS = ("ref must be onshape://<did>/<wid>/<eid>, onshape://<did>/<w|v|m>/<id>/<eid> or an Onshape document URL "
             "https://<host>/documents/<did>/<w|v|m>/<id>/e/<eid>")
FEATURE_SKELETON = ('{"btType": "BTMFeature-134", "featureType": "...", "name": "...", "namespace": "", '
                    '"parameters": [...]}')
EVAL_LAMBDA_ONLY = ("only a lambda function(context is Context, queries is map) { ... } can be evaluated (Onshape "
                    "evalFeatureScript)")
FOREIGN = ("{what} was not created by this aces-onshape process: pass confirm_foreign (MCP) or --confirm-foreign (CLI) "
           "only after checking it is yours to change, and create a version first (onshape_version_create) so there "
           "is a restore point")
PUBLIC = ("document {did} is public (BTDocumentInfo.public): anything written into it is published; pass allow_public "
          "(MCP) or --allow-public (CLI) only if that is intended")
PUBLIC_CREATED = ("Onshape created this document as PUBLIC (a free Onshape plan makes documents public); writes into it "
                  "are refused unless allow_public is given; do not put project geometry in it")
NO_SPECS = ("the code defines features but Onshape reported no feature specs: it may not compile; instantiate it "
            "(onshape_add_custom_feature) or evaluate to see the error")


# --- 9.1 validators -----------------------------------------------------------------------------------------------

def _shown(value) -> str:
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def validate_label(value, arg: str = "label") -> str:
    if not isinstance(value, str) or not re.fullmatch(LABEL_RE, value):
        raise UsageError(f"{arg} must be 1-40 letters, digits, '.', '_' or '-'; got {_shown(value)}")
    return value


def validate_name(value, arg: str = "name") -> str:
    if not isinstance(value, str) or not re.fullmatch(NAME_RE, value):
        raise UsageError(f"{arg} must be 1-256 characters without control characters; got {_shown(value)}")
    return value


def validate_feature_type(value, arg: str = "feature_type") -> str:
    return validate_name(value, arg)


def _text(value, arg: str, lo: int, hi: int) -> str:
    if not isinstance(value, str) or not lo <= len(value) <= hi:
        raise UsageError(f"{arg} must be a string of {lo} to {hi} characters")
    return value


def _bool(value, arg: str) -> bool:
    if not isinstance(value, bool):
        raise UsageError(f"{arg} must be true or false; got {_shown(value)}")
    return value


def _query_text(value, arg: str) -> str:
    if not isinstance(value, str) or len(value) > 200 or re.search(r"[\x00-\x1f\x7f]", value):
        raise UsageError(f"{arg} must be at most 200 characters without control characters")
    return value


def _limit(value, arg: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20:
        raise UsageError(f"{arg} must be an integer from 1 to 20; got {_shown(value)}")
    return value


def _description(value, arg: str) -> str:
    if not isinstance(value, str) or len(value) > 1000 or "\x00" in value:
        raise UsageError(f"{arg} must be a string of at most 1000 characters")
    return value


def _element_type(value, arg: str) -> str:
    if not isinstance(value, str) or value.upper() not in ELEMENT_TYPES:
        raise UsageError(f"{arg} must be one of {', '.join(ELEMENT_TYPES)} (any case); got {_shown(value)}")
    return value.upper()


def _code(value, arg: str) -> str:
    return _text(value, arg, 1, 1_000_000)


def _script(value, arg: str) -> str:
    _text(value, arg, 1, 200_000)
    if not value.strip().startswith("function"):
        raise UsageError(EVAL_LAMBDA_ONLY)
    return value


def _queries(value, arg: str) -> dict:
    if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, list) and all(isinstance(x, str) for x in v) for k, v in value.items()):
        raise UsageError(f"{arg} must be an object mapping names to arrays of deterministic ids (strings)")
    return value


def _feature(value, arg: str) -> dict:
    if (not isinstance(value, dict) or not isinstance(value.get("btType"), str)
            or not value["btType"].startswith("BTM") or not isinstance(value.get("featureType"), str)):
        raise UsageError(f"{arg} must be a BTMFeature-134 map with a btType starting with BTM and a string featureType, "
                         f"for example {FEATURE_SKELETON}")
    return value


def _parameters(value, arg: str) -> dict:
    if not isinstance(value, dict):
        raise UsageError(f"{arg} must be an object mapping parameter ids to values")
    return value


def _units(value, arg: str) -> str:
    if not isinstance(value, str) or value.lower() not in STL_UNITS:
        raise UsageError(f"{arg} must be one of {', '.join(STL_UNITS)}; got {_shown(value)}")
    return value.lower()


def _wv_step(value, arg: str) -> str:
    if value == "m":
        raise UsageError(STEP_NEEDS_WV)
    return validate_wvm(value, arg, ("w", "v"))


def _ref(value, arg: str) -> str:
    parse_ref(value)
    return value


def parse_ref(ref, *, allow_microversion: bool = False) -> dict:
    """{"did", "wvm", "wvmid", "eid", "canonical", "host"} from onshape://... or a browser URL; no request is made."""
    if not isinstance(ref, str) or not re.fullmatch(REF_RAW_RE, ref):
        raise UsageError(REF_FORMS + " (printable ASCII without spaces, at most 512 characters)")
    host = None
    if ref.startswith("onshape://"):
        parts = ref[len("onshape://"):].split("/")
        if len(parts) == 3:
            did, wvmid, eid = parts
            wvm = "w"
        elif len(parts) == 4:
            did, wvm, wvmid, eid = parts
        else:
            raise UsageError(REF_FORMS)
    elif ref.lower().startswith("https://"):
        try:
            parts = urlsplit(ref)
            host = (parts.hostname or "").lower()
        except ValueError:
            raise UsageError(REF_FORMS) from None
        m = re.fullmatch(r"/documents/([^/]+)/([^/]+)/([^/]+)/e/([^/]+)/?", parts.path)
        if not m or not host:
            raise UsageError(REF_FORMS)
        did, wvm, wvmid, eid = m.groups()
    else:
        raise UsageError(REF_FORMS)
    if wvm not in ("w", "v", "m"):
        raise UsageError(REF_FORMS)
    did = validate_id(did, "the document id in ref")
    wvmid = validate_id(wvmid, "the workspace, version or microversion id in ref")
    eid = validate_id(eid, "the element id in ref")
    if wvm == "m" and not allow_microversion:
        raise UsageError(STEP_NEEDS_WV)
    canonical = f"onshape://{did}/{wvmid}/{eid}" if wvm == "w" else f"onshape://{did}/{wvm}/{wvmid}/{eid}"
    return {"did": did, "wvm": wvm, "wvmid": wvmid, "eid": eid, "canonical": canonical, "host": host}


def check_ref_host(parsed: dict, base_url: str) -> None:
    """A document URL must name the configured API host; its host is never used for a request."""
    host = parsed.get("host")
    if not host:
        return
    base_host = (urlsplit(base_url).hostname or "").lower()
    if host.lower() != base_host:
        raise UsageError(f"refusing a document URL on host {host}: the configured Onshape API is on {base_host} "
                         f"(ONSHAPE_BASE_URL); the adapter never sends requests to a host named in a reference")


# Arguments of every operation: (required, validator). `project` is the dispatcher's.
_ID = (True, validate_id)
_OPT_ID = (False, validate_id)
_WVM = (True, validate_wvm)
_FLAG = (False, _bool)
FIELDS: dict[str, dict[str, tuple[bool, Callable]]] = {
    "whoami": {},
    "documents": {"query": (False, _query_text), "limit": (False, _limit)},
    "create_document": {"name": (True, validate_name), "description": (False, _description)},
    "elements": {"did": _ID, "wvm": _WVM, "wvmid": _ID, "element_type": (False, _element_type)},
    "featurestudio_read": {"did": _ID, "wvm": _WVM, "wvmid": _ID, "eid": _ID},
    "featurestudio_write": {"did": _ID, "wid": _ID, "code": (True, _code), "eid": _OPT_ID,
                            "name": (False, validate_name), "confirm_foreign": _FLAG, "allow_public": _FLAG},
    "featurescript_eval": {"did": _ID, "wvm": _WVM, "wvmid": _ID, "eid": _ID, "script": (True, _script),
                           "queries": (False, _queries), "raw": _FLAG},
    "features": {"did": _ID, "wvm": _WVM, "wvmid": _ID, "eid": _ID, "raw": _FLAG},
    "add_feature": {"did": _ID, "wid": _ID, "eid": _ID, "feature": (True, _feature), "allow_public": _FLAG},
    "add_custom_feature": {"did": _ID, "wid": _ID, "eid": _ID, "studio_eid": _ID,
                           "feature_type": (True, validate_feature_type), "parameters": (False, _parameters),
                           "name": (False, validate_name), "pin_version": _FLAG,
                           "replace_feature_id": (False, validate_feature_id), "confirm_foreign": _FLAG,
                           "allow_public": _FLAG},
    "update_feature": {"did": _ID, "wid": _ID, "eid": _ID, "feature_id": (True, validate_feature_id),
                       "feature": (True, _feature), "confirm_foreign": _FLAG, "allow_public": _FLAG},
    "delete_feature": {"did": _ID, "wid": _ID, "eid": _ID, "feature_id": (True, validate_feature_id),
                       "confirm_foreign": _FLAG, "allow_public": _FLAG},
    "version_create": {"did": _ID, "name": (True, validate_name), "wid": _OPT_ID,
                       "description": (False, _description), "allow_public": _FLAG},
    "massproperties": {"did": _ID, "wvm": _WVM, "wvmid": _ID, "eid": _ID},
    "export_step": {"did": _ID, "wvm": (True, _wv_step), "wvmid": _ID, "eid": _ID, "label": (False, validate_label)},
    "export_stl": {"did": _ID, "wvm": _WVM, "wvmid": _ID, "eid": _ID, "label": (False, validate_label),
                   "units": (False, _units)},
    "pull": {"ref": (True, _ref)},
}


def validate_args(name: str, kwargs: Mapping) -> dict:
    """Validate an operation's arguments without network; returns them normalised (absent optionals left out)."""
    fields = FIELDS[name]
    unknown = sorted(k for k in kwargs if k not in fields)
    if unknown:
        raise UsageError(f"unexpected argument(s) for {name}: {', '.join(unknown)}")
    out = {}
    for key, (required, check) in fields.items():
        value = kwargs.get(key)
        if value is None:
            if required:
                raise UsageError(f"{key} is required")
            continue
        out[key] = check(value, key)
    return out


# --- 9.2 the session ----------------------------------------------------------------------------------------------

@dataclass
class Session:
    created_documents: set[str] = field(default_factory=set)                 # did
    created_studios: set[tuple[str, str]] = field(default_factory=set)       # (did, studio eid)
    created_features: set[tuple[str, str, str]] = field(default_factory=set)  # (did, part studio eid, feature id)
    public: dict[str, bool] = field(default_factory=dict)                    # did -> BTDocumentInfo.public, read once
    doc_names: dict[str, str] = field(default_factory=dict)                  # did -> document name, read with public

    def owns_studio(self, did: str, eid: str) -> bool:
        return did in self.created_documents or (did, eid) in self.created_studios

    def owns_feature(self, did: str, eid: str, fid: str) -> bool:
        return did in self.created_documents or (did, eid, fid) in self.created_features


def _guard_foreign(owned: bool, confirm_foreign: bool, what: str) -> None:
    if not owned and not confirm_foreign:
        raise WriteRefused(FOREIGN.format(what=what))


def _remember(session: Session, did: str, doc: dict) -> None:
    session.public[did] = bool(doc.get("public"))
    name = doc.get("name")
    session.doc_names[did] = name if isinstance(name, str) else None


def _document(client: Client, session: Session, did: str, *, refresh: bool = False) -> dict:
    """{"id", "name", "public"} of a document, read once per session unless `refresh`."""
    if refresh or did not in session.public or did not in session.doc_names:
        _remember(session, did, client.get_document(did))
    return {"id": did, "name": session.doc_names.get(did), "public": session.public[did]}


def guard_public(client: Client, session: Session, did: str, allow_public: bool) -> None:
    """Refuse a write into a public document unless allow_public; `public` is read once per session."""
    if did not in session.public:
        _remember(session, did, client.get_document(did))
    if session.public[did] and not allow_public:
        raise WriteRefused(PUBLIC.format(did=did))


def make_client(env: Mapping[str, str] | None = None, *, timeout: float | None = None, trace=None) -> Client:
    """The client from the settings (environment or onshape.env). Sends nothing."""
    settings = config.load_settings(env)
    kw = {"trace": trace, "credentials_path": str(config.credentials_path(env))}
    if timeout is not None:
        kw["timeout"] = timeout
    return Client.from_settings(settings, **kw)


# --- helpers ------------------------------------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _stamp(when: datetime) -> str:
    return when.strftime("%Y%m%dT%H%M%SZ")


def _resp_id(value, field_name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{24}", value):
        raise OnshapeError(f"Onshape returned an unexpected {field_name} format")
    return value.lower()


def _element_list(resp) -> list[dict]:
    if not isinstance(resp, list):
        raise OnshapeError("Onshape returned an unexpected elements response (not a list)")
    return [e for e in resp if isinstance(e, dict)]


def _elements_by_id(resp) -> dict[str, dict]:
    return {e["id"].lower(): e for e in _element_list(resp) if isinstance(e.get("id"), str)}


def _compact_element(e: dict) -> dict:
    return {"id": e.get("id"), "name": e.get("name"), "element_type": e.get("elementType"),
            "microversion_id": e.get("microversionId")}


def _encode_json(obj) -> bytes:
    """Indented JSON as UTF-8. A lone surrogate (legal as a JSON escape in Onshape's responses) can only sit inside a
    JSON string, so backslashreplace writes it as that same escape, \\ud800, instead of failing the write."""
    return (json.dumps(obj, indent=1, ensure_ascii=False) + "\n").encode("utf-8", "backslashreplace")


def _json_bytes(client: Client, obj) -> tuple[bytes, bool, object]:
    redacted, changed = client.redact_obj(obj)
    return _encode_json(redacted), changed, redacted


def _redaction_warning(where: str) -> str:
    return f"a value matching the API credentials was redacted from {where}"


def _spec_summary(specs: list) -> list[dict]:
    out = []
    for s in specs or []:
        if not isinstance(s, dict):
            continue
        params = []
        for p in s.get("parameters") or s.get("allParameters") or []:
            if not isinstance(p, dict):
                continue
            item = {"id": p.get("parameterId"), "bt_type": p.get("btType")}
            if p.get("quantityType"):
                item["quantity_type"] = p["quantityType"]
            if p.get("enumName"):
                item["enum_name"] = p["enumName"]
            if p.get("parameterName"):
                item["name"] = p["parameterName"]
            params.append(item)
        out.append({"feature_type": s.get("featureType"), "feature_type_name": s.get("featureTypeName"),
                    "namespace": s.get("namespace"), "parameters": params})
    return out


def _specs(resp) -> list[dict]:
    specs = resp.get("featureSpecs") if isinstance(resp, dict) else None
    return [s for s in specs or [] if isinstance(s, dict)]


# --- 10. read-only operations -------------------------------------------------------------------------------------

def whoami(client: Client) -> dict:
    info = client.session_info()
    company = info.get("company")
    return {
        "id": info.get("id"),
        "name": info.get("name"),
        "email": info.get("email"),
        "plan_group": info.get("planGroup"),
        "company": company.get("name") if isinstance(company, dict) else company,
        "oauth2_scopes": info.get("oauth2Scopes"),
        "base_url": client.base_url,
        "credentials_source": getattr(client, "credentials_source", None),
    }


def documents(client: Client, query: str = "", limit: int = 20) -> dict:
    a = validate_args("documents", {"query": query, "limit": limit})
    query, limit = a.get("query", ""), a.get("limit", 20)
    resp = client.list_documents(q=query, limit=limit)
    docs = []
    for item in (resp.get("items") if isinstance(resp, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        ws = item.get("defaultWorkspace")
        docs.append({"id": item.get("id"), "name": item.get("name"),
                     "default_workspace_id": ws.get("id") if isinstance(ws, dict) else None,
                     "modified_at": item.get("modifiedAt")})
    return {"query": query, "documents": docs, "has_more": bool(isinstance(resp, dict) and resp.get("next"))}


def elements(client: Client, did: str, wvm: str, wvmid: str, element_type: str | None = None) -> dict:
    a = validate_args("elements", {"did": did, "wvm": wvm, "wvmid": wvmid, "element_type": element_type})
    wanted = a.get("element_type")
    els = _element_list(client.elements(a["did"], a["wvm"], a["wvmid"]))
    return {"elements": [_compact_element(e) for e in els
                         if wanted is None or str(e.get("elementType") or "").upper() == wanted]}


def featurestudio_read(client: Client, did: str, wvm: str, wvmid: str, eid: str) -> dict:
    a = validate_args("featurestudio_read", {"did": did, "wvm": wvm, "wvmid": wvmid, "eid": eid})
    r = client.feature_studio_contents(a["did"], a["wvm"], a["wvmid"], a["eid"])
    contents = r.get("contents") if isinstance(r.get("contents"), str) else ""
    return {"did": a["did"], "wvm": a["wvm"], "wvmid": a["wvmid"], "eid": a["eid"], "contents": contents,
            "contents_sha256": _sha256(contents), "header": parse_header(contents),
            "microversion": r.get("sourceMicroversion")}


def features(client: Client, did: str, wvm: str, wvmid: str, eid: str, raw: bool = False) -> dict:
    a = validate_args("features", {"did": did, "wvm": wvm, "wvmid": wvmid, "eid": eid, "raw": raw})
    r = client.part_studio_features(a["did"], a["wvm"], a["wvmid"], a["eid"])
    states = r.get("featureStates") or {}
    feats = [compact_feature(f, states) for f in r.get("features") or [] if isinstance(f, dict)]
    out = {"microversion": r.get("sourceMicroversion"), "library_version": r.get("libraryVersion"),
           "rollback_index": r.get("rollbackIndex"), "count": len(feats), "features": feats}
    if a.get("raw"):
        out["raw"] = r
    return out


def massproperties(client: Client, did: str, wvm: str, wvmid: str, eid: str) -> dict:
    a = validate_args("massproperties", {"did": did, "wvm": wvm, "wvmid": wvmid, "eid": eid})
    r = client.mass_properties(a["did"], a["wvm"], a["wvmid"], a["eid"])
    return {"microversion": r.get("microversionId"), "bodies": r.get("bodies"),
            "note": "values as Onshape returns them; the schema states no units; mass needs a material"}


# --- 10. operations that write in Onshape -------------------------------------------------------------------------

def create_document(client: Client, name: str, *, project: str, session: Session | None = None,
                    description: str | None = None) -> dict:
    aces_project.check_cloud_allowed(project)
    session = session if session is not None else Session()
    a = validate_args("create_document", {"name": name, "description": description})
    info = client.create_document(a["name"], a.get("description"))
    did = _resp_id(info.get("id"), "document id")
    ws = info.get("defaultWorkspace")
    wid = _resp_id(ws.get("id") if isinstance(ws, dict) else None, "workspace id")
    public = bool(info.get("public"))
    session.created_documents.add(did)
    _remember(session, did, info)
    els = _element_list(client.elements(did, "w", wid))
    warnings = [PUBLIC_CREATED] if public else []
    return {"did": did, "wid": wid, "name": info.get("name"), "public": public,
            "elements": [_compact_element(e) for e in els], "warnings": warnings}


def featurestudio_write(client: Client, did: str, wid: str, code: str, *, project: str,
                        session: Session | None = None, eid: str | None = None, name: str | None = None,
                        confirm_foreign: bool = False, allow_public: bool = False) -> dict:
    aces_project.check_cloud_allowed(project)
    session = session if session is not None else Session()
    a = validate_args("featurestudio_write", {"did": did, "wid": wid, "code": code, "eid": eid, "name": name,
                                              "confirm_foreign": confirm_foreign, "allow_public": allow_public})
    did, wid, code, eid, name = a["did"], a["wid"], a["code"], a.get("eid"), a.get("name")
    if eid is not None:
        _guard_foreign(session.owns_studio(did, eid), a.get("confirm_foreign", False),
                       f"Feature Studio {eid} in document {did}")
    guard_public(client, session, did, a.get("allow_public", False))
    created = False
    if eid is None:
        studio_name = name or DEFAULT_STUDIO_NAME
        info = client.create_feature_studio(did, wid, studio_name)
        eid = _resp_id(info.get("id"), "Feature Studio id")
        session.created_studios.add((did, eid))
        created = True
        name = info.get("name") if isinstance(info.get("name"), str) else studio_name
    else:
        name = None
    current = client.feature_studio_contents(did, "w", wid, eid)
    current_text = current.get("contents") if isinstance(current.get("contents"), str) else ""
    written, prepended, warnings = ensure_header(code, current_text)
    w = client.update_feature_studio_contents(did, wid, eid, written)
    specs: list = []
    specs_error = None
    try:
        specs = _specs(client.feature_studio_specs(did, "w", wid, eid))
    except OnshapeError as e:
        if e.status is not None and 400 <= e.status < 500 and e.status not in (401, 403, 404, 429):
            specs_error = str(e)
        else:
            raise
    if "defineFeature" in code and not specs:
        warnings.append(NO_SPECS)
    out = {"did": did, "wid": wid, "eid": eid, "created": created, "name": name,
           "microversion": w.get("sourceMicroversion") if isinstance(w, dict) else None,
           "contents_sha256": _sha256(written),
           "previous_contents_sha256": _sha256(current_text),
           "previous_microversion": current.get("sourceMicroversion"),
           "header": parse_header(written), "header_prepended": prepended,
           "feature_specs": _spec_summary(specs), "specs_error": specs_error}
    notices = w.get("notices") if isinstance(w, dict) else None
    error_notice = False
    if isinstance(notices, list) and notices:
        out["notices"] = [compact_notice(n) for n in notices if isinstance(n, dict)]
        error_notice = any(n.get("level") == "ERROR" for n in out["notices"])
    out["ok"] = specs_error is None and not error_notice
    out["warnings"] = warnings
    return out


def featurescript_eval(client: Client, did: str, wvm: str, wvmid: str, eid: str, script: str, *, project: str,
                       queries: dict | None = None, raw: bool = False) -> dict:
    aces_project.check_cloud_allowed(project)
    a = validate_args("featurescript_eval", {"did": did, "wvm": wvm, "wvmid": wvmid, "eid": eid, "script": script,
                                             "queries": queries, "raw": raw})
    r = client.eval_featurescript(a["did"], a["wvm"], a["wvmid"], a["eid"], a["script"], a.get("queries"))
    notices = [compact_notice(n) for n in r.get("notices") or [] if isinstance(n, dict)]
    out = {"did": a["did"], "wvm": a["wvm"], "wvmid": a["wvmid"], "eid": a["eid"],
           "ok": not any(n.get("level") == "ERROR" for n in notices),
           "result": decode_fsvalue(r.get("result")), "console": r.get("console"), "notices": notices,
           "microversion": r.get("sourceMicroversion")}
    if a.get("raw"):
        out["result_raw"] = r.get("result")
    return out


def _feature_result(client: Client, did: str, wid: str, eid: str, r: dict, *, replaced: bool) -> dict:
    f = r.get("feature") if isinstance(r.get("feature"), dict) else {}
    fid = f.get("featureId")
    if not isinstance(fid, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", fid):
        raise OnshapeError("Onshape returned an unexpected featureId format; the feature may have been written: "
                           "check the document (onshape_features)")
    state = r.get("featureState") if isinstance(r.get("featureState"), dict) else {}
    status = state.get("featureStatus")
    warnings: list[str] = []
    messages = None
    if status != "OK":
        try:
            probe = client.eval_featurescript(did, "w", wid, eid, feature_messages_script(fid))
            messages = decode_fsvalue(probe.get("result"))
        except AuthError:
            raise
        except OnshapeError as e:
            messages = None
            warnings.append(f"could not read the feature's error text: {e}")
    return {"did": did, "wid": wid, "eid": eid, "feature_id": fid, "name": f.get("name"),
            "feature_type": f.get("featureType"), "feature_status": status, "ok": status != "ERROR",
            "messages": messages, "microversion": r.get("sourceMicroversion"),
            "feature": compact_feature(f, {fid: state}), "replaced": replaced, "warnings": warnings}


def add_feature(client: Client, did: str, wid: str, eid: str, feature: dict, *, project: str,
                session: Session | None = None, allow_public: bool = False) -> dict:
    aces_project.check_cloud_allowed(project)
    session = session if session is not None else Session()
    a = validate_args("add_feature", {"did": did, "wid": wid, "eid": eid, "feature": feature,
                                      "allow_public": allow_public})
    did, wid, eid = a["did"], a["wid"], a["eid"]
    guard_public(client, session, did, a.get("allow_public", False))
    r = client.add_feature(did, wid, eid, a["feature"])
    out = _feature_result(client, did, wid, eid, r, replaced=False)
    session.created_features.add((did, eid, out["feature_id"]))
    return out


def update_feature(client: Client, did: str, wid: str, eid: str, feature_id: str, feature: dict, *, project: str,
                   session: Session | None = None, confirm_foreign: bool = False, allow_public: bool = False) -> dict:
    aces_project.check_cloud_allowed(project)
    session = session if session is not None else Session()
    a = validate_args("update_feature", {"did": did, "wid": wid, "eid": eid, "feature_id": feature_id,
                                         "feature": feature, "confirm_foreign": confirm_foreign,
                                         "allow_public": allow_public})
    did, wid, eid, fid = a["did"], a["wid"], a["eid"], a["feature_id"]
    _guard_foreign(session.owns_feature(did, eid, fid), a.get("confirm_foreign", False),
                   f"feature {fid} in Part Studio {eid} of document {did}")
    guard_public(client, session, did, a.get("allow_public", False))
    r = client.update_feature(did, wid, eid, fid, a["feature"])
    return _feature_result(client, did, wid, eid, r, replaced=True)


def delete_feature(client: Client, did: str, wid: str, eid: str, feature_id: str, *, project: str,
                   session: Session | None = None, confirm_foreign: bool = False, allow_public: bool = False) -> dict:
    aces_project.check_cloud_allowed(project)
    session = session if session is not None else Session()
    a = validate_args("delete_feature", {"did": did, "wid": wid, "eid": eid, "feature_id": feature_id,
                                         "confirm_foreign": confirm_foreign, "allow_public": allow_public})
    did, wid, eid, fid = a["did"], a["wid"], a["eid"], a["feature_id"]
    _guard_foreign(session.owns_feature(did, eid, fid), a.get("confirm_foreign", False),
                   f"feature {fid} in Part Studio {eid} of document {did}")
    guard_public(client, session, did, a.get("allow_public", False))
    r = client.delete_feature(did, wid, eid, fid)
    session.created_features.discard((did, eid, fid))
    return {"deleted": fid, "microversion": r.get("sourceMicroversion"),
            "already_deleted_on_retry": bool(r.get("already_deleted"))}


def add_custom_feature(client: Client, did: str, wid: str, eid: str, studio_eid: str, feature_type: str,
                       parameters: dict | None = None, *, project: str, session: Session | None = None,
                       name: str | None = None, pin_version: bool = False, replace_feature_id: str | None = None,
                       confirm_foreign: bool = False, allow_public: bool = False,
                       now: datetime | None = None) -> dict:
    """Instantiate a custom feature from a Feature Studio (section 10.3 of the spec)."""
    aces_project.check_cloud_allowed(project)
    session = session if session is not None else Session()
    a = validate_args("add_custom_feature", {
        "did": did, "wid": wid, "eid": eid, "studio_eid": studio_eid, "feature_type": feature_type,
        "parameters": parameters, "name": name, "pin_version": pin_version, "replace_feature_id": replace_feature_id,
        "confirm_foreign": confirm_foreign, "allow_public": allow_public})
    did, wid, eid, seid = a["did"], a["wid"], a["eid"], a["studio_eid"]
    feature_type, values = a["feature_type"], a.get("parameters") or {}
    replace = a.get("replace_feature_id")
    if replace is not None:
        _guard_foreign(session.owns_feature(did, eid, replace), a.get("confirm_foreign", False),
                       f"feature {replace} in Part Studio {eid} of document {did}")
    guard_public(client, session, did, a.get("allow_public", False))
    warnings: list[str] = []

    # 2. the workspace first, in both modes: an unknown type or bad parameters fail before any version exists
    by_id = _elements_by_id(client.elements(did, "w", wid))
    studio, part_studio = by_id.get(seid), by_id.get(eid)
    if not studio or studio.get("elementType") != "FEATURESTUDIO":
        raise UsageError(f"studio_eid {seid} is not a Feature Studio in workspace {wid} "
                         f"({(studio or {}).get('elementType') or 'not found'})")
    if not part_studio or part_studio.get("elementType") != "PARTSTUDIO":
        raise UsageError(f"eid {eid} is not a Part Studio in workspace {wid} "
                         f"({(part_studio or {}).get('elementType') or 'not found'})")
    spec = find_spec(_specs(client.feature_studio_specs(did, "w", wid, seid)), feature_type)
    computed = workspace_namespace(seid, _resp_id(studio.get("microversionId"), "element microversionId"))
    build_parameters(spec, values, feature_namespace=computed)  # validation only; nothing is sent yet
    ps_specs: list = []
    ps_error = None
    try:
        ps_specs = _specs(client.part_studio_specs(did, "w", wid, eid))
    except AuthError:
        raise
    except OnshapeError as e:
        ps_error = str(e)
    match = same_type = None
    for s in ps_specs:
        if s.get("featureType") != spec.get("featureType"):
            continue
        if same_type is None:
            same_type = s
        parsed = parse_namespace(s.get("namespace"))
        if match is None and parsed and parsed["element_id"] == seid:
            match = s
    ps_namespace = (match or same_type or {}).get("namespace") if (match or same_type) else None

    version_id = None
    if not a.get("pin_version", False):
        if match is not None:
            ns, source = match.get("namespace"), "partstudio_featurespecs"
        else:
            ns, source = computed, "computed"
        spec_used, ctx = spec, ("w", wid)
    else:
        stamp = _stamp(_now(now))
        room = 256 - len(f"ACES pin  {stamp}")
        vinfo = client.create_version(did, wid, f"ACES pin {feature_type[:room]} {stamp}")
        version_id = _resp_id(vinfo.get("id"), "version id")
        remains = f"; version {version_id} was created and remains"
        try:
            vstudio = _elements_by_id(client.elements(did, "v", version_id)).get(seid)
            if not vstudio:
                raise UsageError(f"Feature Studio {seid} is not in version {version_id}")
            mv = _resp_id(vstudio.get("microversionId"), "element microversionId")
            spec_used = find_spec(_specs(client.feature_studio_specs(did, "v", version_id, seid)), feature_type)
        except AdapterError as e:
            e.args = (f"{e}{remains}",)
            raise
        ns, source = version_namespace(did, version_id, seid, mv), "version"
        ctx = ("v", version_id)

    pspecs = [p for p in (spec_used.get("parameters") or spec_used.get("allParameters") or []) if isinstance(p, dict)]
    enum_spec_ns = {p.get("parameterId"): p.get("namespace") or "" for p in pspecs
                    if p.get("btType") == "BTParameterSpecEnum-171"}
    studio_enums: frozenset = frozenset()
    if enum_spec_ns:
        try:
            src = client.feature_studio_contents(did, ctx[0], ctx[1], seid).get("contents") or ""
            studio_enums = frozenset(declared_enums(src if isinstance(src, str) else ""))
        except AuthError:
            raise
        except OnshapeError as e:
            warnings.append(f"could not read the Feature Studio source to find the enums it exports ({e}); enum "
                            f"parameters keep the namespace Onshape reported")
    params = build_parameters(spec_used, values, feature_namespace=ns, studio_enums=studio_enums)
    feature = {"btType": "BTMFeature-134", "featureType": spec_used.get("featureType"),
               "name": a.get("name") or spec_used.get("featureTypeName") or spec_used.get("featureType"),
               "namespace": ns, "parameters": params}
    if replace is not None:
        r = client.update_feature(did, wid, eid, replace, feature)
    else:
        r = client.add_feature(did, wid, eid, feature)
    out = _feature_result(client, did, wid, eid, r, replaced=replace is not None)
    if replace is None:
        session.created_features.add((did, eid, out["feature_id"]))
    sent = []
    for p in params:
        item = compact_parameter(p)
        if p.get("btType") == "BTMParameterEnum-145":
            item["spec_namespace"] = enum_spec_ns.get(p.get("parameterId"), "")
        sent.append(item)
    parsed = parse_namespace(ns)
    out.update({
        "namespace": ns,
        "namespace_form": (("version" if parsed["version_id"] else "workspace") if parsed else None),
        "namespace_source": source,
        "computed_namespace": computed,
        "featurespec_namespace": spec_used.get("namespace"),
        "partstudio_featurespec_namespace": ps_namespace,
        "version_id": version_id,
        "parameters_sent": sent,
    })
    if ps_error is not None:
        out["partstudio_featurespecs_error"] = ps_error
    out["warnings"] = warnings + out["warnings"]
    return out


def version_create(client: Client, did: str, name: str, *, project: str, session: Session | None = None,
                   wid: str | None = None, description: str | None = None, allow_public: bool = False) -> dict:
    aces_project.check_cloud_allowed(project)
    session = session if session is not None else Session()
    a = validate_args("version_create", {"did": did, "name": name, "wid": wid, "description": description,
                                         "allow_public": allow_public})
    did, wid = a["did"], a.get("wid")
    if wid is None:
        doc = client.get_document(did)
        _remember(session, did, doc)
        ws = doc.get("defaultWorkspace")
        wid = _resp_id(ws.get("id") if isinstance(ws, dict) else None, "defaultWorkspace id")
    guard_public(client, session, did, a.get("allow_public", False))
    r = client.create_version(did, wid, a["name"], a.get("description") or "")
    return {"did": did, "wid": wid, "version_id": r.get("id"), "name": r.get("name"),
            "microversion": r.get("microversion"), "created_at": r.get("createdAt")}


# --- 10.5 exports -------------------------------------------------------------------------------------------------

def _unlink(*paths: Path) -> None:
    for p in paths:
        try:
            p.unlink()
        except OSError:
            pass


def _reserve(outdir: Path, base: str, ext: str) -> Path:
    """Reserve <base>[-n]<ext> by exclusive create (n = 2 .. 99 on collisions)."""
    for n in range(1, 100):
        candidate = outdir / f"{base}{'' if n == 1 else f'-{n}'}{ext}"
        if candidate.with_name(candidate.name + ".json").exists():
            continue
        try:
            with open(candidate, "xb"):
                pass
            return candidate
        except FileExistsError:
            continue
    raise AdapterError(f"no free export file name for {base}{ext} in {outdir} after 99 tries")


def _export(client: Client, fmt: str, did: str, wvm: str, wvmid: str, eid: str, *, info: dict, session: Session,
            label: str | None, now: datetime | None, units: str | None = None) -> dict:
    doc = _document(client, session, did)
    if wvm in ("w", "v"):
        mv = client.current_microversion(did, wvm, wvmid).get("microversion")
    else:
        mv = wvmid
    when = _now(now)
    outdir = Path(info["dir"]) / "cad" / "onshape" / "exports"
    outdir.mkdir(parents=True, exist_ok=True)
    ext = ".step" if fmt == "STEP" else ".stl"
    path = _reserve(outdir, f"{_stamp(when)}-{eid[:8]}" + (f"-{label}" if label else ""), ext)
    partial = path.with_name(path.name + ".partial")
    sidecar = path.with_name(path.name + ".json")
    sidecar_made = False
    try:
        if fmt == "STEP":
            got = client.export_step(did, wvm, wvmid, eid, partial)
        else:
            got = client.export_stl(did, wvm, wvmid, eid, partial, units=units)
        os.replace(partial, path)
        side = {"schema": "aces.cad.onshape.export/1", "format": fmt, "file": path.name, "sha256": got["sha256"],
                "bytes": got["bytes"], "ref": f"onshape://{did}/{wvm}/{wvmid}/{eid}", "document": doc,
                "microversion": mv, "exported_at": utc_z(when)}
        if fmt == "STEP":
            side["translation"] = got["translation"]
        else:
            side["stl"] = {"units": units, "mode": "binary", "grouping": True}
        side["project"] = info
        side["tool"] = TOOL
        data, _changed, _red = _json_bytes(client, side)
        with open(sidecar, "xb") as fh:
            sidecar_made = True
            fh.write(data)
    except BaseException:
        _unlink(path, partial)
        if sidecar_made:
            _unlink(sidecar)
        raise
    out = {"path": str(path), "sidecar": str(sidecar), "sha256": got["sha256"], "bytes": got["bytes"],
           "microversion": mv, "document_public": doc["public"]}
    if fmt == "STEP":
        out["translation_id"] = got["translation"]["id"]
    out["project"] = info
    return out


def export_step(client: Client, did: str, wvm: str, wvmid: str, eid: str, *, project: str,
                session: Session | None = None, label: str | None = None, now: datetime | None = None) -> dict:
    info = aces_project.check_cloud_allowed(project)
    session = session if session is not None else Session()
    a = validate_args("export_step", {"did": did, "wvm": wvm, "wvmid": wvmid, "eid": eid, "label": label})
    return _export(client, "STEP", a["did"], a["wvm"], a["wvmid"], a["eid"], info=info, session=session,
                   label=a.get("label"), now=now)


def export_stl(client: Client, did: str, wvm: str, wvmid: str, eid: str, *, project: str,
               session: Session | None = None, label: str | None = None, units: str = "millimeter",
               now: datetime | None = None) -> dict:
    info = aces_project.check_cloud_allowed(project)
    session = session if session is not None else Session()
    a = validate_args("export_stl", {"did": did, "wvm": wvm, "wvmid": wvmid, "eid": eid, "label": label,
                                     "units": units})
    return _export(client, "STL", a["did"], a["wvm"], a["wvmid"], a["eid"], info=info, session=session,
                   label=a.get("label"), now=now, units=a.get("units", "millimeter"))


# --- 10.4 pull ----------------------------------------------------------------------------------------------------

def _namespaces(feature_list) -> list[str]:
    """Every distinct non-empty namespace over the features and their subFeatures, in order of appearance."""
    seen: set[str] = set()
    out: list[str] = []

    def walk(items) -> None:
        for f in items or []:
            if not isinstance(f, dict):
                continue
            ns = f.get("namespace")
            if isinstance(ns, str) and ns and ns not in seen:
                seen.add(ns)
                out.append(ns)
            walk(f.get("subFeatures"))

    walk(feature_list)
    return out


def _capture_studios(client: Client, did: str, wvm: str, wvmid: str, pulled: dict[str, dict], feature_list,
                     warnings: list[str]) -> tuple[list[dict], list[str], bool]:
    """The Feature Studio sources the features use, and their imports, transitively within the bounds."""
    contexts: dict[tuple[str, str, str], dict[str, dict]] = {(did, wvm, wvmid): pulled}
    failed: dict[tuple[str, str, str], str] = {}  # a context whose elements call failed is not asked again
    studios: dict[tuple[str, str, str, str], dict] = {}
    order: list[tuple[str, str, str, str]] = []
    unrecognised: list[str] = []
    complete = True
    queue: deque = deque()
    for ns in _namespaces(feature_list):
        parsed = parse_namespace(ns)
        if parsed is None:
            unrecognised.append(ns)
            complete = False
            continue
        ctx = (parsed["document_id"], "v", parsed["version_id"]) if parsed["version_id"] else (did, wvm, wvmid)
        queue.append((ctx + (parsed["element_id"],), 0, {"namespace": ns}, parsed["microversion"], None))
    reads = 0

    def stale(seid: str, expected, current) -> None:
        warnings.append(f"Feature Studio {seid} changed after the instance was created (namespace m{expected}, "
                        f"studio now m{current}); the captured source is the current one, not necessarily the code "
                        f"the feature regenerated with")

    def unread(key, reacher, imp, reason: str, *, counts: bool = True, field_name: str = "error") -> None:
        nonlocal complete
        if counts:
            complete = False
        if imp is not None:
            imp["fetched"] = False
            imp["reason"] = reason
        else:
            studios[key] = {"document_id": key[0], "wvm": key[1], "wvmid": key[2], "element_id": key[3],
                            "reached_by": [reacher], field_name: reason}
            order.append(key)

    while queue:
        key, depth, reacher, expected, imp = queue.popleft()
        entry = studios.get(key)
        if entry is not None:
            entry["reached_by"].append(reacher)
            if imp is not None:
                if "contents" in entry:
                    imp["fetched"] = True
                    imp["current"] = imp["version"] == entry.get("microversion_current")
                else:
                    imp["fetched"] = False
                    imp["reason"] = entry.get("error") or entry.get("skipped") or "not read"
            elif "contents" in entry and expected != entry.get("microversion_current"):
                stale(key[3], expected, entry.get("microversion_current"))
            continue
        if imp is not None and depth > IMPORT_DEPTH:
            unread(key, reacher, imp, f"depth limit {IMPORT_DEPTH}")
            continue
        if reads >= STUDIO_LIMIT:
            unread(key, reacher, imp, f"studio limit {STUDIO_LIMIT}", field_name="skipped")
            continue
        sdid, swvm, swvmid, seid = key
        ctx = (sdid, swvm, swvmid)
        if ctx in failed:
            unread(key, reacher, imp, failed[ctx])
            continue
        try:
            if ctx not in contexts:
                contexts[ctx] = _elements_by_id(client.elements(sdid, swvm, swvmid))
            element = contexts[ctx].get(seid)
        except AuthError:
            raise
        except OnshapeError as e:
            failed[ctx] = str(e)
            unread(key, reacher, imp, str(e))
            continue
        if element is None:
            unread(key, reacher, imp, "element not found")
            continue
        etype = element.get("elementType")
        if etype != "FEATURESTUDIO":
            unread(key, reacher, imp, f"not a Feature Studio ({etype})", counts=imp is None)
            continue
        reads += 1
        current = element.get("microversionId")
        try:
            r = client.feature_studio_contents(sdid, swvm, swvmid, seid)
        except AuthError:
            raise
        except OnshapeError as e:
            complete = False
            studios[key] = {"document_id": sdid, "wvm": swvm, "wvmid": swvmid, "element_id": seid,
                            "reached_by": [reacher], "error": str(e)}
            order.append(key)
            if imp is not None:
                imp["fetched"] = False
                imp["reason"] = str(e)
            continue
        contents = r.get("contents") if isinstance(r.get("contents"), str) else ""
        imports = parse_imports(contents)
        studios[key] = {
            "document_id": sdid, "wvm": swvm, "wvmid": swvmid, "element_id": seid, "name": element.get("name"),
            "reached_by": [reacher], "microversion_expected": expected, "microversion_current": current,
            "namespace_current": expected == current, "read_from": f"{swvm}/{swvmid}",
            "source_microversion": r.get("sourceMicroversion"), "contents_sha256": _sha256(contents),
            "contents": contents, "imports": imports}
        order.append(key)
        if imp is not None:
            imp["fetched"] = True
            imp["current"] = imp["version"] == current
        elif expected != current:
            stale(seid, expected, current)
        for item in imports:
            if item["kind"] == "std":
                item["fetched"] = False
                item["reason"] = "std library"
                continue
            if item["kind"] == "unrecognised":
                item["fetched"] = False
                item["reason"] = "unrecognised path"
                complete = False
                continue
            if item["kind"] == "workspace":
                target = (sdid, swvm, swvmid, item["element_id"])
            else:
                target = (item["document_id"], "v", item["version_id"], item["element_id"])
            via = {"imported_by": seid, "document_id": sdid, "read_from": f"{swvm}/{swvmid}", "path": item["path"]}
            queue.append((target, depth + 1, via, item["version"], item))
    return [studios[k] for k in order], unrecognised, complete


def _bundle_name(base: str, n: int) -> str:
    return base if n == 1 else f"{base}-{n}"


def pull(client: Client, ref: str, *, project: str, session: Session | None = None,
         now: datetime | None = None) -> dict:
    """The CadSource pull: part.step, parameters.json, source.json and outcome.json under
    <ACES_WORK>/<project>/cad/onshape/<UTC stamp>-<eid8>[-n]/, plus a cad.pull ledger entry where aces offers one."""
    info = aces_project.check_cloud_allowed(project)
    r = parse_ref(ref)
    check_ref_host(r, client.base_url)
    session = session if session is not None else Session()
    did, wvm, wvmid, eid = r["did"], r["wvm"], r["wvmid"], r["eid"]
    warnings: list[str] = []

    doc = _document(client, session, did, refresh=True)
    pulled = _elements_by_id(client.elements(did, wvm, wvmid))
    element = pulled.get(eid)
    if element is None or element.get("elementType") != "PARTSTUDIO":
        kind = element.get("elementType") if element else f"not in document {did} at {wvm}/{wvmid}"
        raise UsageError(f"element {eid} is {kind}; pull needs a Part Studio")
    m_before = client.current_microversion(did, wvm, wvmid).get("microversion")
    fl = client.part_studio_features(did, wvm, wvmid, eid)
    if not isinstance(fl, dict):
        raise OnshapeError("Onshape returned an unexpected feature list (not an object)")
    studios, unrecognised, complete = _capture_studios(client, did, wvm, wvmid, pulled, fl.get("features"),
                                                       warnings)
    try:
        mp = client.mass_properties(did, wvm, wvmid, eid)
        mass = {"microversion_id": mp.get("microversionId"), "bodies": mp.get("bodies")}
    except AuthError:
        raise
    except OnshapeError as e:
        mass = {"error": str(e)}

    base_dir = Path(info["dir"]) / "cad" / "onshape"
    base_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{_stamp(_now(now))}-{eid[:8]}"
    n = 0
    for n in range(1, 100):
        candidate = _bundle_name(stem, n)
        if (base_dir / candidate).exists() or (base_dir / f".{candidate}.partial").exists():
            continue
        try:
            os.mkdir(base_dir / f".{candidate}.partial")
            break
        except FileExistsError:
            continue
    else:
        raise AdapterError(f"no free bundle name for {stem} in {base_dir} after 99 tries")
    tmp = base_dir / f".{_bundle_name(stem, n)}.partial"
    states = fl.get("featureStates") or {}
    try:
        step = client.export_step(did, wvm, wvmid, eid, tmp / "part.step")
        exported_at = utc_z(_now(now))
        m_after = client.current_microversion(did, wvm, wvmid).get("microversion")
        consistent = m_after == m_before
        if not consistent:
            warnings.append(f"the workspace changed during the pull (microversion {m_before} -> {m_after}); pull a "
                            f"version for an immutable source")
        parameters = {
            "schema": "aces.cad.onshape.parameters/1",
            "ref": r["canonical"],
            "part_studio": {"element_id": eid, "name": element.get("name"),
                            "microversion_id": element.get("microversionId")},
            "source_microversion": fl.get("sourceMicroversion"),
            "library_version": fl.get("libraryVersion"),
            "rollback_index": fl.get("rollbackIndex"),
            "features": [compact_feature(f, states) for f in fl.get("features") or [] if isinstance(f, dict)],
            "feature_studios_complete": complete,
            "unrecognised_namespaces": unrecognised,
            "feature_studios": studios,
            "mass_properties": mass,
            "feature_list": fl,
        }
        params_bytes, changed, _red = _json_bytes(client, parameters)
        if changed:
            warnings.append(_redaction_warning("parameters.json"))
        (tmp / "parameters.json").write_bytes(params_bytes)
        files = {"part.step": {"sha256": step["sha256"], "bytes": step["bytes"]},
                 "parameters.json": {"sha256": hashlib.sha256(params_bytes).hexdigest(), "bytes": len(params_bytes)}}
        # asked now, so source.json (written once) says when no OS ledger entry is written; outcome.json, written after
        # the rename, says whether an available `ledger record` succeeded
        ledger_cap = aces_project.ledger_capability()
        source = {
            "schema": "aces.cad.onshape.source/1",
            "source": "onshape",
            "ref": r["canonical"],
            "base_url": client.base_url,
            "document": {"id": did, "name": doc["name"], "public": doc["public"]},
            "workspace_id": wvmid if wvm == "w" else None,
            "version_id": wvmid if wvm == "v" else None,
            "element": {"id": eid, "name": element.get("name"), "type": "PARTSTUDIO",
                        "microversion_id": element.get("microversionId")},
            "microversion": {"before": m_before, "after": m_after, "consistent": consistent},
            "exported_at": exported_at,
            "translation": step["translation"],
            "files": files,
            "outcome_file": "outcome.json",
            "ledger": {"kind": "cad.pull", "available": ledger_cap["available"], "reason": ledger_cap["reason"]},
            "project": info,
            "tool": TOOL,
            "warnings": list(warnings),
        }
        redacted, changed = client.redact_obj(source)
        if changed:
            warnings.append(_redaction_warning("source.json"))
            redacted["warnings"] = list(warnings)
        source_bytes = _encode_json(redacted)
        (tmp / "source.json").write_bytes(source_bytes)
        files["source.json"] = {"sha256": hashlib.sha256(source_bytes).hexdigest(), "bytes": len(source_bytes)}
        # source.json cannot hold its own hash: outcome.json carries it, so it is on disk even when no ledger entry is
        pending = {"schema": "aces.cad.onshape.outcome/1",
                   "files": {"source.json": files["source.json"]},
                   "ledger": {"written": False, "kind": "cad.pull", "reason": "pending"},
                   "cad_source": {"recorded": False, "reason": "pending"}}
        (tmp / "outcome.json").write_bytes(_json_bytes(client, pending)[0])
        while True:
            name = _bundle_name(stem, n)
            target = base_dir / name
            if not target.exists():
                try:
                    os.rename(tmp, target)
                    break
                except FileExistsError:
                    pass
                except OSError as e:
                    if e.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                        raise
            n += 1
            if n > 99:
                raise AdapterError(f"no free bundle name for {stem} in {base_dir} after 99 tries")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    rel = f"cad/onshape/{name}/part.step"
    payload = {"source": "onshape", "ref": r["canonical"], "project": info["name"], "bundle": str(target),
               "document_name": doc["name"], "base_url": client.base_url, "exported_at": exported_at,
               "microversion_before": m_before, "microversion_after": m_after, "consistent": consistent,
               "translation_id": step["translation"]["id"],
               "files": {k: v["sha256"] for k, v in files.items()}}
    payload, changed = client.redact_obj(payload)
    if changed:
        warnings.append(_redaction_warning("the ledger payload"))
    ledger = aces_project.ledger_record("cad.pull", payload, capability=ledger_cap)
    cad_source = aces_project.add_cad_source(info["name"], rel)
    outcome = {"schema": "aces.cad.onshape.outcome/1", "files": {"source.json": files["source.json"]},
               "ledger": ledger, "cad_source": cad_source, "updated_at": utc_z()}
    data, changed, _red = _json_bytes(client, outcome)
    if changed:
        warnings.append(_redaction_warning("outcome.json"))
    scratch = target / "outcome.json.tmp"
    scratch.write_bytes(data)
    os.replace(scratch, target / "outcome.json")
    return {"bundle": str(target), "ref": r["canonical"], "files": files, "ledger": ledger, "cad_source": cad_source,
            "consistent": consistent, "document_public": doc["public"], "feature_studios_complete": complete,
            "warnings": warnings}


# --- 9.3 dispatch -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Operation:
    func: Callable
    project_tool: bool
    takes_session: bool


OPERATIONS: dict[str, Operation] = {
    "whoami": Operation(whoami, False, False),
    "documents": Operation(documents, False, False),
    "create_document": Operation(create_document, True, True),
    "elements": Operation(elements, False, False),
    "featurestudio_read": Operation(featurestudio_read, False, False),
    "featurestudio_write": Operation(featurestudio_write, True, True),
    "featurescript_eval": Operation(featurescript_eval, True, False),
    "features": Operation(features, False, False),
    "add_feature": Operation(add_feature, True, True),
    "add_custom_feature": Operation(add_custom_feature, True, True),
    "update_feature": Operation(update_feature, True, True),
    "delete_feature": Operation(delete_feature, True, True),
    "version_create": Operation(version_create, True, True),
    "massproperties": Operation(massproperties, False, False),
    "export_step": Operation(export_step, True, True),
    "export_stl": Operation(export_stl, True, True),
    "pull": Operation(pull, True, True),
}
PROJECT_TOOLS = tuple(k for k, v in OPERATIONS.items() if v.project_tool)


def dispatch(name: str, kwargs: Mapping | None, *, session: Session | None = None,
             env: Mapping[str, str] | None = None, timeout: float | None = None, trace=None,
             on_client: Callable[[Client], None] | None = None):
    """Run one operation in the order of section 9.3; a failure at any step before 4 sends no request."""
    op = OPERATIONS[name]
    kwargs = dict(kwargs or {})
    project = kwargs.pop("project", None)
    args = validate_args(name, kwargs)                       # 1. arguments, no network
    if op.project_tool:                                      # 2. the class gate
        project = aces_project.resolve_project(project, env)
        aces_project.check_cloud_allowed(project, env)
    else:
        aces_project.gate_from_env(env)
    client = make_client(env, timeout=timeout, trace=trace)  # 3. the client (no request)
    if on_client is not None:
        on_client(client)
    if not op.project_tool:                                  # 4. the operation
        return op.func(client, **args)
    if op.takes_session:
        args["session"] = session if session is not None else Session()
    return op.func(client, project=project, **args)
