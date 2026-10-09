"""Test helpers: an in-process fake of the Onshape REST API, the network guard, and the base TestCase.

Everything here is a test fixture. Ids are random, names and texts say "(fixture)", and nothing the fake returns is
Onshape output: it implements the documented request and response shapes (OpenAPI 1.221.89963) with in-memory state.

The network guard is installed when this module is imported, so it covers the whole test run: a connection to any
host other than the loopback fakes raises NonLoopbackConnection and is recorded in BLOCKED, and
AdapterTestCase.tearDown asserts that BLOCKED is empty, so even code that swallows every exception cannot hide one.
Child processes get an environment from subprocess_env(): no ONSHAPE_*, ACES_*, proxy or colour variables from the
operator, a temporary HOME, USERPROFILE and ACES dirs, and a base URL that is the fake or https://onshape.invalid.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import os
import re
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from collections import namedtuple
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

PKG_ROOT = Path(__file__).resolve().parent.parent
SOURCE_TREE = (PKG_ROOT / "aces_onshape" / "__init__.py").exists()
if SOURCE_TREE and str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))  # a source tree; installed tests use the system package

TESTS_DIR = Path(__file__).resolve().parent
FAKE_ACES = TESTS_DIR / "fake_aces.py"

# --- the network guard --------------------------------------------------------------------------------------------

BLOCKED: list[str] = []
_REAL_CREATE_CONNECTION = getattr(socket.create_connection, "_aces_real", socket.create_connection)
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class NonLoopbackConnection(RuntimeError):
    """A test tried to reach a host other than the loopback fakes (not an OSError, so urllib does not wrap it)."""


def _guarded_create_connection(address, *args, **kwargs):
    host = str(address[0]).lower()
    if host not in LOOPBACK:
        BLOCKED.append(f"{host}:{address[1]}")
        raise NonLoopbackConnection(f"test tried to connect to {host}:{address[1]}; tests may only reach the loopback "
                                    f"fakes")
    return _REAL_CREATE_CONNECTION(address, *args, **kwargs)


_guarded_create_connection._aces_real = _REAL_CREATE_CONNECTION
socket.create_connection = _guarded_create_connection

# --- fixture constants (none of these is a real credential, signature or Onshape value) ---------------------------

ACCESS = "ACCESS-fixture-key-0001"
SECRET = "SECRET-fixture-value-7f3a9c2e"
SIGNATURE = "SIGNATURE-fixture-0001"
INVALID_BASE_URL = "https://onshape.invalid/api/v17"
# fixture header; the number is arbitrary, not Onshape's current version
FIXTURE_HEADER = 'FeatureScript 2000;\nimport(path : "onshape/std/common.fs", version : "2000.0");\n'
STL_FIXTURE = b"solid aces-fixture\nendsolid aces-fixture\n"
JSON_TYPE = "application/json;charset=UTF-8; qs=0.09"
PROXY_VARS = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
DROP_VARS = {"FORCE_COLOR", "PYTHON_COLORS", "PYTHONPATH"}

# A FeatureScript studio for the tests (fixture code, not run by Onshape here): two lengths and a boolean.
PLATE_CODE = """annotation { "Feature Type Name" : "ACES plate" }
export const acesPlate = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        annotation { "Name" : "Length" }
        isLength(definition.length, NONNEGATIVE_LENGTH_BOUNDS);
        annotation { "Name" : "Width" }
        isLength(definition.width, NONNEGATIVE_LENGTH_BOUNDS);
        annotation { "Name" : "Flag" }
        definition.flag is boolean;
    }
    {
    });
"""


def basic(access: str = ACCESS, secret: str = SECRET) -> str:
    return base64.b64encode(f"{access}:{secret}".encode("utf-8")).decode("ascii")


def _strip_env(env: dict) -> dict:
    for key in list(env):
        if key.startswith(("ONSHAPE_", "ACES_")) or key in DROP_VARS or key.upper() in PROXY_VARS:
            del env[key]
    return env


def subprocess_env(tmpdir, *, fake=None, keys: bool = False, extra: dict | None = None) -> dict:
    """An environment for a child process: nothing of the operator's Onshape or ACES setup, loopback or .invalid."""
    tmp = Path(tmpdir)
    env = _strip_env(dict(os.environ))
    home = tmp / "home"
    home.mkdir(parents=True, exist_ok=True)
    env.update({"HOME": str(home), "USERPROFILE": str(home), "ACES_USER_CONFIG": str(tmp / "config"),
                "ACES_WORK": str(tmp / "work"), "ACES_STATE": str(tmp / "state"), "ACES_ONSHAPE_ACES_CMD": "[]",
                "ACES_ONSHAPE_POLL_S": "0.01", "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1"})
    if SOURCE_TREE:
        env["PYTHONPATH"] = str(PKG_ROOT)
    if fake is not None:
        env["ONSHAPE_BASE_URL"] = fake.base_url
        env["ACES_ONSHAPE_ALLOW_HTTP_LOOPBACK"] = "1"
    else:
        env["ONSHAPE_BASE_URL"] = INVALID_BASE_URL
    if keys:
        env["ONSHAPE_ACCESS_KEY"] = fake.access if fake is not None else ACCESS
        env["ONSHAPE_SECRET_KEY"] = fake.secret if fake is not None else SECRET
    if extra:
        env.update(extra)
    return env


def fs_value(value) -> dict:
    """A Python value as a BTFSValue-1888 (for fixture eval results)."""
    if value is None:
        return {"btType": "BTFSValueUndefined-2003"}
    if isinstance(value, bool):
        return {"btType": "BTFSValueBoolean-1195", "value": value}
    if isinstance(value, (int, float)):
        return {"btType": "BTFSValueNumber-772", "value": value}
    if isinstance(value, str):
        return {"btType": "BTFSValueString-1422", "value": value}
    if isinstance(value, list):
        return {"btType": "BTFSValueArray-1499", "value": [fs_value(v) for v in value]}
    if isinstance(value, dict):
        return {"btType": "BTFSValueMap-2062", "value": [
            {"btType": "BTFSValueMapEntry-2077", "key": fs_value(k), "value": fs_value(v)} for k, v in value.items()]}
    raise TypeError(type(value).__name__)


DEFAULT_EVAL_RESULT = fs_value({"note": "eval result (fixture)"})

# --- the fixture feature specs ------------------------------------------------------------------------------------

_FEATURE_RE = re.compile(r"export\s+const\s+(\w+)\s*=\s*defineFeature")
_TYPE_NAME_RE = re.compile(r'"Feature Type Name"\s*:\s*"([^"]*)"')
_PARAM_RE = re.compile(r"is(Length|Angle|Integer)\(\s*definition\.(\w+)|definition\.(\w+)\s+is\s+(\w+)")
_ENUM_RE = re.compile(r"export\s+enum\s+(\w+)\s*\{")


def fixture_enums(contents: str) -> dict[str, list[str]]:
    """export enum T { ... } -> option identifiers, annotations removed (fixture parsing, not FeatureScript's)."""
    out = {}
    for m in _ENUM_RE.finditer(contents):
        depth, i = 1, m.end()
        while i < len(contents) and depth:
            depth += {"{": 1, "}": -1}.get(contents[i], 0)
            i += 1
        body = re.sub(r"annotation\s*\{[^{}]*\}", " ", contents[m.end():i - 1])
        out[m.group(1)] = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", body)
    return out


def fixture_specs(contents: str, namespace: str = "") -> list[dict]:
    """BTFeatureSpec-129 maps for every `export const X = defineFeature` in the contents (fixture rules)."""
    features = list(_FEATURE_RE.finditer(contents))
    names = list(_TYPE_NAME_RE.finditer(contents))
    enums = fixture_enums(contents)
    specs = []
    for i, m in enumerate(features):
        prev_end = features[i - 1].end() if i else 0
        end = features[i + 1].start() if i + 1 < len(features) else len(contents)
        type_name = None
        for n in names:
            if prev_end <= n.start() < m.start():
                type_name = n.group(1)
        params, seen = [], set()
        for pm in _PARAM_RE.finditer(contents, m.end(), end):
            if pm.group(1):
                qtype = {"Length": "LENGTH", "Angle": "ANGLE", "Integer": "INTEGER"}[pm.group(1)]
                pid = pm.group(2)
                expr = {"LENGTH": "1 mm", "ANGLE": "1 deg", "INTEGER": "1"}[qtype]
                spec = {"btType": "BTParameterSpecQuantity-173", "parameterId": pid, "parameterName": pid,
                        "quantityType": qtype,
                        "defaultValue": {"btType": "BTMParameterQuantity-147", "parameterId": pid, "expression": expr,
                                         "isInteger": qtype == "INTEGER"}}
            else:
                pid, kind = pm.group(3), pm.group(4)
                if kind == "boolean":
                    spec = {"btType": "BTParameterSpecBoolean-170", "parameterId": pid, "parameterName": pid,
                            "defaultValue": {"btType": "BTMParameterBoolean-144", "parameterId": pid, "value": False}}
                elif kind == "string":
                    spec = {"btType": "BTParameterSpecString-175", "parameterId": pid, "parameterName": pid,
                            "defaultValue": {"btType": "BTMParameterString-149", "parameterId": pid, "value": ""}}
                elif kind == "Query":
                    spec = {"btType": "BTParameterSpecQuery-174", "parameterId": pid, "parameterName": pid}
                else:
                    options = enums.get(kind, [])
                    spec = {"btType": "BTParameterSpecEnum-171", "parameterId": pid, "parameterName": pid,
                            "enumName": kind, "namespace": "", "options": options}
                    if kind in enums and options:
                        spec["defaultValue"] = {"btType": "BTMParameterEnum-145", "parameterId": pid,
                                                "enumName": kind, "value": options[0], "namespace": ""}
            if pid in seen:
                continue
            seen.add(pid)
            params.append(spec)
        specs.append({"btType": "BTFeatureSpec-129", "featureType": m.group(1),
                      "featureTypeName": type_name or m.group(1), "namespace": namespace, "parameters": params})
    return specs


# --- the fake server ----------------------------------------------------------------------------------------------

Request = namedtuple("Request", "method path query headers body")
H = r"([0-9a-f]{24})"
ROUTES = [
    ("GET", r"/users/sessioninfo", "sessioninfo"),
    ("GET", r"/documents", "list_documents"),
    ("POST", r"/documents", "create_document"),
    ("GET", rf"/documents/{H}", "get_document"),
    ("GET", rf"/documents/d/{H}/([wvm])/{H}/elements", "elements"),
    ("GET", rf"/documents/d/{H}/([wv])/{H}/currentmicroversion", "current_microversion"),
    ("POST", rf"/documents/d/{H}/versions", "create_version"),
    ("POST", rf"/featurestudios/d/{H}/w/{H}", "create_studio"),
    ("GET", rf"/featurestudios/d/{H}/([wvm])/{H}/e/{H}", "studio_contents"),
    ("POST", rf"/featurestudios/d/{H}/([wvm])/{H}/e/{H}", "studio_update"),
    ("GET", rf"/featurestudios/d/{H}/([wvm])/{H}/e/{H}/featurespecs", "studio_specs"),
    ("GET", rf"/partstudios/d/{H}/([wvm])/{H}/e/{H}/featurespecs", "partstudio_specs"),
    ("POST", rf"/partstudios/d/{H}/([wvm])/{H}/e/{H}/featurescript", "eval"),
    ("GET", rf"/partstudios/d/{H}/([wvm])/{H}/e/{H}/features", "features"),
    ("POST", rf"/partstudios/d/{H}/([wvm])/{H}/e/{H}/features", "add_feature"),
    ("POST", rf"/partstudios/d/{H}/w/{H}/e/{H}/features/featureid/([^/]+)", "update_feature"),
    ("DELETE", rf"/partstudios/d/{H}/w/{H}/e/{H}/features/featureid/([^/]+)", "delete_feature"),
    ("GET", rf"/partstudios/d/{H}/([wvm])/{H}/e/{H}/massproperties", "massproperties"),
    ("GET", rf"/partstudios/d/{H}/([wvm])/{H}/e/{H}/stl", "stl"),
    ("POST", rf"/partstudios/d/{H}/([wv])/{H}/e/{H}/translations", "start_translation"),
    ("GET", r"/translations/([^/]+)", "get_translation"),
    ("GET", rf"/documents/d/{H}/externaldata/([^/]+)", "external_data"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeOnshape/0.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def do_DELETE(self):
        self._serve("DELETE")

    def _serve(self, method: str) -> None:
        fake = self.server
        split = urllib.parse.urlsplit(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(split.query, keep_blank_values=True).items()}
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        try:
            status, hdrs, data = fake.respond(Request(method, split.path, query, headers, body))
        except Exception as e:  # noqa: BLE001 (a bug in the fake: recorded for tearDown, answered as 500)
            fake.errors.append(f"{method} {split.path}: {type(e).__name__}: {e}")
            status, hdrs, data = 500, {"Content-Type": JSON_TYPE}, b'{"message": "fake server error (fixture)"}'
        try:
            self.send_response(status)
            for key, value in hdrs.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)
        except (ConnectionError, BrokenPipeError, TimeoutError):
            pass  # the client may have timed out and gone


class FakeOnshape(ThreadingHTTPServer):
    """A fake Onshape REST API on 127.0.0.1 with in-memory state (fixtures only)."""

    daemon_threads = True
    block_on_close = False

    def __init__(self, access: str = ACCESS, secret: str = SECRET):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.access = access
        self.secret = secret
        self.origin = f"http://127.0.0.1:{self.server_address[1]}"
        self.base_url = self.origin + "/api/v17"
        self.prefix = "/api/v17"
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.user_id = secrets.token_hex(12)
        self.docs: dict[str, dict] = {}
        self.translations: dict[str, dict] = {}
        self.external: dict[str, str] = {}
        self.requests: list[Request] = []
        self.download_auth: list[bool] = []
        self.errors: list[str] = []
        self._injections: list[dict] = []
        self._delays: list[dict] = []
        # knobs
        self.eval_result = copy.deepcopy(DEFAULT_EVAL_RESULT)
        self.eval_console = ""
        self.eval_notices: list = []
        self.last_eval: dict | None = None
        self.next_feature_status = "OK"
        self.translation_mode = "ok"
        self.bad_translation_id: str | None = None
        self.bad_result_document_id: str | None = None
        self.stl_redirect_origin: str | None = None
        self.force_public = False
        self.partstudio_spec_namespace: dict[str, str] = {}

    # --- lifecycle and inspection ---------------------------------------------------------------------------------

    def server_bind(self):
        """HTTPServer.server_bind without its socket.getfqdn() reverse lookup: the tests make no DNS queries."""
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]

    def start(self) -> "FakeOnshape":
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.thread is not None:
            self.shutdown()
            self.thread = None
        self.server_close()

    def count(self, method: str | None = None, path_contains: str | None = None) -> int:
        return len(self.matching(method, path_contains))

    def matching(self, method: str | None = None, path_contains: str | None = None) -> list[Request]:
        with self.lock:
            return [r for r in self.requests if (method is None or r.method == method)
                    and (path_contains is None or path_contains in r.path)]

    def last(self, method: str | None = None, path_contains: str | None = None) -> Request:
        found = self.matching(method, path_contains)
        if not found:
            raise AssertionError(f"no {method or ''} request matching {path_contains!r}")
        return found[-1]

    def inject(self, status: int, *, path_contains: str | None = None, method: str | None = None,
               headers: dict | None = None, body=None, count: int = 1, apply: bool = False) -> None:
        with self.lock:
            self._injections.append({"status": status, "path_contains": path_contains, "method": method,
                                     "headers": dict(headers or {}), "body": body, "count": count, "apply": apply})

    def delay(self, seconds: float, *, path_contains: str | None = None, method: str | None = None,
              count: int = 1) -> None:
        with self.lock:
            self._delays.append({"seconds": seconds, "path_contains": path_contains, "method": method,
                                 "count": count})

    def _take(self, rules: list[dict], method: str, path: str) -> dict | None:
        with self.lock:
            for rule in rules:
                if rule["count"] <= 0:
                    continue
                if rule["method"] is not None and rule["method"] != method:
                    continue
                if rule["path_contains"] is not None and rule["path_contains"] not in path:
                    continue
                rule["count"] -= 1
                return rule
        return None

    # --- request handling -----------------------------------------------------------------------------------------

    def respond(self, req: Request):
        with self.lock:
            self.requests.append(req)
        if req.path.startswith("/fake-download/"):
            with self.lock:
                self.download_auth.append("authorization" in req.headers)
        elif not req.path.startswith(self.prefix + "/") and req.path != self.prefix:
            return self._json(404, {"message": "Not found (fixture)", "status": 404})
        elif req.headers.get("authorization") != "Basic " + basic(self.access, self.secret):
            return self._json(401, {"message": "Unauthorized (fixture)", "status": 401})
        delay = self._take(self._delays, req.method, req.path)
        if delay is not None:
            time.sleep(delay["seconds"])
        injected = self._take(self._injections, req.method, req.path)
        if injected is not None and not injected["apply"]:
            return self._injected(injected)
        if req.path.startswith("/fake-download/"):
            answer = (200, {"Content-Type": "application/octet-stream"}, STL_FIXTURE)
        else:
            answer = self._route(req)
        return self._injected(injected) if injected is not None else answer

    def _injected(self, rule: dict):
        body = rule["body"]
        if body is None:
            body = {"message": f"injected HTTP {rule['status']} (fixture)", "status": rule["status"]}
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = bytes(body)
        headers = {"Content-Type": JSON_TYPE}
        headers.update(rule["headers"])
        return rule["status"], headers, data

    @staticmethod
    def _json(status: int, obj, headers: dict | None = None):
        out = {"Content-Type": JSON_TYPE}
        out.update(headers or {})
        return status, out, json.dumps(obj).encode("utf-8")

    def _not_found(self):
        return self._json(404, {"message": "Not found (fixture)", "status": 404})

    def _bad(self, text: str):
        return self._json(400, {"message": f"{text} (fixture)", "status": 400})

    def _route(self, req: Request):
        api = req.path[len(self.prefix):]
        for method, pattern, name in ROUTES:
            if method != req.method:
                continue
            m = re.fullmatch(pattern, api)
            if m:
                args = [urllib.parse.unquote(g) for g in m.groups()]
                body = None
                if req.body:
                    try:
                        body = json.loads(req.body.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        return self._bad("request body is not JSON")
                with self.lock:
                    return getattr(self, "_r_" + name)(req, body, *args)
        return self._not_found()

    # --- state helpers --------------------------------------------------------------------------------------------

    @staticmethod
    def _element(eid: str, name: str, etype: str, contents: str | None = None) -> dict:
        return {"id": eid, "name": name, "elementType": etype, "microversionId": secrets.token_hex(12),
                "contents": contents, "features": [], "states": {}}

    def _new_document(self, name: str, public: bool) -> dict:
        did, wid = secrets.token_hex(12), secrets.token_hex(12)
        ps, asm = secrets.token_hex(12), secrets.token_hex(12)
        doc = {"id": did, "name": name, "public": public, "wid": wid, "microversion": secrets.token_hex(12),
               "elements": {ps: self._element(ps, "Part Studio 1", "PARTSTUDIO"),
                            asm: self._element(asm, "Assembly 1", "ASSEMBLY")},
               "versions": {}, "modifiedAt": _now_iso()}
        self.docs[did] = doc
        return doc

    def _touch(self, doc: dict, element: dict | None = None) -> None:
        doc["microversion"] = secrets.token_hex(12)
        doc["modifiedAt"] = _now_iso()
        if element is not None:
            element["microversionId"] = secrets.token_hex(12)

    def _context(self, did: str, wvm: str, wvmid: str):
        """(document, elements, microversion) of a workspace, version or microversion, or None."""
        doc = self.docs.get(did)
        if doc is None:
            return None
        if wvm == "w":
            return (doc, doc["elements"], doc["microversion"]) if wvmid == doc["wid"] else None
        if wvm == "v":
            v = doc["versions"].get(wvmid)
            return (doc, v["elements"], v["microversion"]) if v else None
        if wvmid == doc["microversion"]:
            return doc, doc["elements"], doc["microversion"]
        for v in doc["versions"].values():
            if v["microversion"] == wvmid:
                return doc, v["elements"], v["microversion"]
        return None

    def _workspace(self, did: str, wid: str):
        doc = self.docs.get(did)
        return doc if doc is not None and doc["wid"] == wid else None

    @staticmethod
    def _info(e: dict) -> dict:
        return {"id": e["id"], "name": e["name"], "elementType": e["elementType"], "microversionId": e["microversionId"]}

    def _doc_info(self, doc: dict) -> dict:
        return {"id": doc["id"], "name": doc["name"], "jsonType": "document", "public": doc["public"],
                "defaultWorkspace": {"id": doc["wid"]}, "modifiedAt": doc["modifiedAt"]}

    def _feature_response(self, doc: dict, feature: dict, status: str):
        return self._json(200, {"btType": "BTFeatureDefinitionResponse-1617", "feature": feature,
                                "featureState": {"btType": "BTFeatureState-1688", "featureStatus": status},
                                "sourceMicroversion": doc["microversion"]})

    def _take_status(self) -> str:
        status = self.next_feature_status
        self.next_feature_status = "OK"
        return status

    # --- routes ---------------------------------------------------------------------------------------------------

    def _r_sessioninfo(self, req, body):
        return self._json(200, {"id": self.user_id, "name": "Fixture User", "email": "fixture@example.invalid",
                                "jsonType": "user-summary", "planGroup": "fixture"})

    def _r_list_documents(self, req, body):
        q = req.query.get("q", "").lower()
        docs = [d for d in self.docs.values() if q in d["name"].lower()]
        offset = int(req.query.get("offset") or 0)
        limit = int(req.query.get("limit") or 20)
        items = [{"id": d["id"], "name": d["name"], "jsonType": "document-summary",
                  "defaultWorkspace": {"id": d["wid"]}, "modifiedAt": d["modifiedAt"]}
                 for d in docs[offset:offset + limit]]
        return self._json(200, {"items": items, "next": None})

    def _r_create_document(self, req, body):
        if not isinstance(body, dict) or not isinstance(body.get("name"), str) or not body["name"]:
            return self._bad("name is required")
        doc = self._new_document(body["name"], bool(self.force_public or body.get("isPublic")))
        return self._json(200, self._doc_info(doc))

    def _r_get_document(self, req, body, did):
        doc = self.docs.get(did)
        return self._json(200, self._doc_info(doc)) if doc else self._not_found()

    def _r_elements(self, req, body, did, wvm, wvmid):
        ctx = self._context(did, wvm, wvmid)
        if ctx is None:
            return self._not_found()
        return self._json(200, [self._info(e) for e in ctx[1].values()])

    def _r_current_microversion(self, req, body, did, wv, wvid):
        ctx = self._context(did, wv, wvid)
        return self._json(200, {"microversion": ctx[2]}) if ctx else self._not_found()

    def _r_create_version(self, req, body, did):
        doc = self.docs.get(did)
        if doc is None:
            return self._not_found()
        if not isinstance(body, dict) or not body.get("workspaceId") or not body.get("name"):
            return self._bad("workspaceId and name are required")
        if body["workspaceId"] != doc["wid"]:
            return self._not_found()
        vid = secrets.token_hex(12)
        doc["versions"][vid] = {"id": vid, "name": body["name"], "microversion": doc["microversion"],
                                "createdAt": _now_iso(), "elements": copy.deepcopy(doc["elements"])}
        v = doc["versions"][vid]
        return self._json(200, {"id": vid, "name": v["name"], "microversion": v["microversion"], "documentId": did,
                                "createdAt": v["createdAt"]})

    def _r_create_studio(self, req, body, did, wid):
        doc = self._workspace(did, wid)
        if doc is None:
            return self._not_found()
        if not isinstance(body, dict) or not isinstance(body.get("name"), str) or not body["name"]:
            return self._bad("name is required")
        eid = secrets.token_hex(12)
        element = self._element(eid, body["name"], "FEATURESTUDIO", FIXTURE_HEADER)
        doc["elements"][eid] = element
        self._touch(doc)
        return self._json(200, self._info(element))

    def _studio(self, did, wvm, wvmid, eid):
        ctx = self._context(did, wvm, wvmid)
        if ctx is None:
            return None, None
        element = ctx[1].get(eid)
        if element is None or element["elementType"] != "FEATURESTUDIO":
            return ctx, None
        return ctx, element

    def _r_studio_contents(self, req, body, did, wvm, wvmid, eid):
        if wvm == "m":
            return self._not_found()  # fixture: the fake serves studios at w and v only
        ctx, element = self._studio(did, wvm, wvmid, eid)
        if element is None:
            return self._not_found()
        return self._json(200, {"btType": "BTFeatureStudioContents-2239", "contents": element["contents"],
                                "sourceMicroversion": ctx[2]})

    def _r_studio_update(self, req, body, did, wvm, wvmid, eid):
        if wvm != "w":
            return self._bad("Feature Studios can only be written in a workspace")
        ctx, element = self._studio(did, wvm, wvmid, eid)
        if element is None:
            return self._not_found()
        if (not isinstance(body, dict) or body.get("btType") != "BTFeatureStudioContents-2239"
                or not isinstance(body.get("contents"), str)):
            return self._bad("btType must be BTFeatureStudioContents-2239 with string contents")
        element["contents"] = body["contents"]
        self._touch(ctx[0], element)
        return self._json(200, {"btType": "BTFeatureStudioContents-2239", "contents": element["contents"],
                                "sourceMicroversion": ctx[0]["microversion"]})

    def _r_studio_specs(self, req, body, did, wvm, wvmid, eid):
        ctx, element = self._studio(did, wvm, wvmid, eid)
        if element is None:
            return self._not_found()
        contents = element["contents"] or ""
        if "SYNTAX_ERROR_FOR_TEST" in contents:
            return self._json(400, {"message": "Parse error at line 1 (fixture)"})
        return self._json(200, {"btType": "BTFeatureSpecsResponse-664", "featureSpecs": fixture_specs(contents)})

    def _part_studio(self, did, wvm, wvmid, eid):
        ctx = self._context(did, wvm, wvmid)
        if ctx is None:
            return None, None
        element = ctx[1].get(eid)
        if element is None or element["elementType"] != "PARTSTUDIO":
            return ctx, None
        return ctx, element

    def _r_partstudio_specs(self, req, body, did, wvm, wvmid, eid):
        ctx, element = self._part_studio(did, wvm, wvmid, eid)
        if element is None:
            return self._not_found()
        specs = []
        for e in ctx[1].values():
            if e["elementType"] != "FEATURESTUDIO" or "SYNTAX_ERROR_FOR_TEST" in (e["contents"] or ""):
                continue
            ns = self.partstudio_spec_namespace.get(e["id"], f"e{e['id']}::m{e['microversionId']}")
            specs.extend(fixture_specs(e["contents"] or "", ns))
        return self._json(200, {"btType": "BTFeatureSpecsResponse-664", "featureSpecs": specs})

    def _r_eval(self, req, body, did, wvm, wvmid, eid):
        ctx, element = self._part_studio(did, wvm, wvmid, eid)
        if element is None:
            return self._not_found()
        if (not isinstance(body, dict) or body.get("btType") != "BTFeatureScriptEvalCall-2377"
                or not str(body.get("script") or "").strip().startswith("function")):
            return self._bad("btType must be BTFeatureScriptEvalCall-2377 and script a function")
        self.last_eval = body
        return self._json(200, {"btType": "BTFeatureScriptEvalResponse-1859", "result": self.eval_result,
                                "console": self.eval_console, "notices": self.eval_notices,
                                "sourceMicroversion": ctx[2]})

    def _r_features(self, req, body, did, wvm, wvmid, eid):
        ctx, element = self._part_studio(did, wvm, wvmid, eid)
        if element is None:
            return self._not_found()
        states = {fid: {"btType": "BTFeatureState-1688", "featureStatus": s} for fid, s in element["states"].items()}
        return self._json(200, {"btType": "BTFeatureListResponse-2457", "features": element["features"],
                                "featureStates": states, "defaultFeatures": [], "sourceMicroversion": ctx[2],
                                "libraryVersion": 2000, "rollbackIndex": -1})

    def _r_add_feature(self, req, body, did, wvm, wvmid, eid):
        if wvm != "w":
            return self._bad("features can only be added in a workspace")
        ctx, element = self._part_studio(did, wvm, wvmid, eid)
        if element is None:
            return self._not_found()
        if (not isinstance(body, dict) or body.get("btType") != "BTFeatureDefinitionCall-1406"
                or not isinstance(body.get("feature"), dict) or not body["feature"].get("btType")):
            return self._bad("btType must be BTFeatureDefinitionCall-1406 with a feature btType")
        feature = copy.deepcopy(body["feature"])
        feature["featureId"] = "F" + secrets.token_hex(7) + "_0"
        status = self._take_status()
        element["features"].append(feature)
        element["states"][feature["featureId"]] = status
        self._touch(ctx[0], element)
        return self._feature_response(ctx[0], feature, status)

    def _r_update_feature(self, req, body, did, wid, eid, fid):
        ctx, element = self._part_studio(did, "w", wid, eid)
        if element is None:
            return self._not_found()
        if (not isinstance(body, dict) or body.get("btType") != "BTFeatureDefinitionCall-1406"
                or not isinstance(body.get("feature"), dict)):
            return self._bad("btType must be BTFeatureDefinitionCall-1406")
        if body["feature"].get("featureId") != fid:
            return self._bad("feature.featureId must equal the featureid in the path")
        for i, f in enumerate(element["features"]):
            if f.get("featureId") == fid:
                element["features"][i] = copy.deepcopy(body["feature"])
                status = self._take_status()
                element["states"][fid] = status
                self._touch(ctx[0], element)
                return self._feature_response(ctx[0], element["features"][i], status)
        return self._not_found()

    def _r_delete_feature(self, req, body, did, wid, eid, fid):
        ctx, element = self._part_studio(did, "w", wid, eid)
        if element is None:
            return self._not_found()
        for i, f in enumerate(element["features"]):
            if f.get("featureId") == fid:
                del element["features"][i]
                element["states"].pop(fid, None)
                self._touch(ctx[0], element)
                return self._json(200, {"btType": "BTFeatureApiBase-1430", "sourceMicroversion": ctx[0]["microversion"]})
        return self._not_found()

    def _r_massproperties(self, req, body, did, wvm, wvmid, eid):
        ctx, element = self._part_studio(did, wvm, wvmid, eid)
        if element is None:
            return self._not_found()
        return self._json(200, {"bodies": {"-all-": {"hasMass": False, "massMissingCount": 1,
                                                     "volume": [1e-05, 1e-05, 1e-05], "centroid": [0.0, 0.0, 0.0],
                                                     "mass": [0.0, 0.0, 0.0]}},
                                "microversionId": ctx[2]})

    def _r_stl(self, req, body, did, wvm, wvmid, eid):
        ctx, element = self._part_studio(did, wvm, wvmid, eid)
        if element is None:
            return self._not_found()
        origin = self.stl_redirect_origin or self.origin
        location = f"{origin}/fake-download/{secrets.token_hex(8)}?sig={SIGNATURE}"
        return 307, {"Location": location, "Content-Type": JSON_TYPE}, b""

    def _r_start_translation(self, req, body, did, wv, wvid, eid):
        ctx, element = self._part_studio(did, wv, wvid, eid)
        if element is None:
            return self._not_found()
        if not isinstance(body, dict) or not body.get("formatName"):
            return self._bad("formatName is required")
        tid = self.bad_translation_id or secrets.token_hex(12)
        self.translations[tid] = {"did": did, "eid": eid, "microversion": ctx[2], "polls": 0,
                                  "fid": secrets.token_hex(12), "body": body}
        return self._json(200, {"id": tid, "requestState": "ACTIVE", "documentId": did, "requestElementId": eid})

    def _r_get_translation(self, req, body, tid):
        t = self.translations.get(tid)
        if t is None:
            return self._not_found()
        t["polls"] += 1
        info = {"id": tid, "documentId": t["did"], "requestElementId": t["eid"], "requestState": "ACTIVE"}
        if t["polls"] >= 2:
            if self.translation_mode == "fail":
                info.update({"requestState": "FAILED", "failureReason": "fixture failure"})
            else:
                self.external[t["fid"]] = tid
                info.update({"requestState": "DONE", "resultExternalDataIds": [t["fid"]],
                             "resultDocumentId": self.bad_result_document_id or t["did"]})
        return self._json(200, info)

    def _r_external_data(self, req, body, did, fid):
        tid = self.external.get(fid)
        if tid is None or self.translations[tid]["did"] != did:
            return self._not_found()
        return 200, {"Content-Type": "application/octet-stream"}, self.step_bytes(tid)

    # --- fixture data and seeding (direct state changes, no requests: a client Session does not own these) -------

    def step_bytes(self, tid: str) -> bytes:
        t = self.translations[tid]
        return ("ISO-10303-21;\nHEADER;\n"
                "FILE_DESCRIPTION(('aces-cad-onshape test fixture, not Onshape output'),'2;1');\nENDSEC;\n"
                f"/* did={t['did']} eid={t['eid']} microversion={t['microversion']} */\n"
                "END-ISO-10303-21;\n").encode("ascii")

    def seed_document(self, name: str = "Seeded document (fixture)") -> tuple[str, str, str]:
        with self.lock:
            doc = self._new_document(name, public=False)
            ps = next(e["id"] for e in doc["elements"].values() if e["elementType"] == "PARTSTUDIO")
            return doc["id"], doc["wid"], ps

    def seed_studio(self, did: str, wid: str, name: str, contents: str) -> str:
        with self.lock:
            doc = self._workspace(did, wid)
            eid = secrets.token_hex(12)
            doc["elements"][eid] = self._element(eid, name, "FEATURESTUDIO", contents)
            self._touch(doc)
            return eid

    def seed_blob(self, did: str, wid: str, name: str) -> str:
        with self.lock:
            doc = self._workspace(did, wid)
            eid = secrets.token_hex(12)
            doc["elements"][eid] = self._element(eid, name, "BLOB")
            self._touch(doc)
            return eid

    def seed_feature(self, did: str, wid: str, ps_eid: str, feature_map: dict) -> str:
        with self.lock:
            doc = self._workspace(did, wid)
            element = doc["elements"][ps_eid]
            feature = copy.deepcopy(feature_map)
            feature["featureId"] = "F" + secrets.token_hex(7) + "_0"
            element["features"].append(feature)
            element["states"][feature["featureId"]] = "OK"
            self._touch(doc, element)
            return feature["featureId"]

    def seed_version(self, did: str, wid: str, name: str) -> str:
        with self.lock:
            doc = self._workspace(did, wid)
            vid = secrets.token_hex(12)
            doc["versions"][vid] = {"id": vid, "name": name, "microversion": doc["microversion"],
                                    "createdAt": _now_iso(), "elements": copy.deepcopy(doc["elements"])}
            return vid

    def set_studio_contents(self, did: str, eid: str, contents: str) -> None:
        with self.lock:
            doc = self.docs[did]
            element = doc["elements"][eid]
            element["contents"] = contents
            self._touch(doc, element)

    def element(self, did: str, eid: str, vid: str | None = None) -> dict:
        with self.lock:
            doc = self.docs[did]
            els = doc["versions"][vid]["elements"] if vid else doc["elements"]
            return copy.deepcopy(els[eid])

    def feature(self, did: str, eid: str, fid: str) -> dict | None:
        with self.lock:
            for f in self.docs[did]["elements"][eid]["features"]:
                if f.get("featureId") == fid:
                    return copy.deepcopy(f)
            return None

    def versions(self, did: str) -> list[str]:
        with self.lock:
            return list(self.docs[did]["versions"])


# --- the base TestCase --------------------------------------------------------------------------------------------

class AdapterTestCase(unittest.TestCase):
    """Isolated environment, no proxies, an optional fake Onshape, and the network-guard check in tearDown."""

    START_FAKE = True

    def setUp(self):
        BLOCKED.clear()
        self.tmp = Path(tempfile.mkdtemp(prefix="aces-onshape-test-"))
        env_patch = mock.patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        _strip_env(os.environ)
        proxy_patch = mock.patch("urllib.request.getproxies", lambda: {})
        proxy_patch.start()
        self.addCleanup(proxy_patch.stop)
        home = self.tmp / "home"
        home.mkdir()
        os.environ.update({"HOME": str(home), "USERPROFILE": str(home), "ACES_USER_CONFIG": str(self.tmp / "config"),
                           "ACES_WORK": str(self.tmp / "work"), "ACES_STATE": str(self.tmp / "state"),
                           "ACES_ONSHAPE_ACES_CMD": "[]", "ACES_ONSHAPE_POLL_S": "0.01", "NO_COLOR": "1",
                           "PYTHONDONTWRITEBYTECODE": "1"})
        self.sleeps: list[float] = []
        self.fake = None
        if self.START_FAKE:
            self.fake = FakeOnshape().start()
            os.environ.update({"ONSHAPE_BASE_URL": self.fake.base_url, "ONSHAPE_ACCESS_KEY": self.fake.access,
                               "ONSHAPE_SECRET_KEY": self.fake.secret, "ACES_ONSHAPE_ALLOW_HTTP_LOOPBACK": "1"})
        else:
            os.environ["ONSHAPE_BASE_URL"] = INVALID_BASE_URL

    def tearDown(self):
        errors = []
        if self.fake is not None:
            self.fake.stop()
            errors = list(self.fake.errors)
        shutil.rmtree(self.tmp, ignore_errors=True)
        blocked = list(BLOCKED)
        BLOCKED.clear()
        self.assertEqual(blocked, [])
        self.assertEqual(errors, [])

    # --- helpers --------------------------------------------------------------------------------------------------

    def start_fake(self, **kw) -> FakeOnshape:
        fake = FakeOnshape(**kw).start()
        self.addCleanup(fake.stop)
        return fake

    def client(self, **kw):
        from aces_onshape.client import Client
        kw.setdefault("allow_http_loopback", True)
        kw.setdefault("sleep", self.sleeps.append)
        return Client(self.fake.base_url, self.fake.access, self.fake.secret, **kw)

    def make_project(self, name: str, cls: str | None, *, class_line: str | None = None) -> Path:
        """aces.yaml in aces-base's _dump format (class_line, when given, replaces the class line verbatim)."""
        pdir = Path(os.environ["ACES_WORK"]) / name
        pdir.mkdir(parents=True, exist_ok=True)
        line = class_line if class_line is not None else f"class: {'null' if cls is None else cls}"
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        text = (f"name: {name}\n{line}\ncreated: \"{created}\"\ncompany: \"\"\nrd_activity: \"\"\nhypothesis: \"\"\n"
                "cad_sources: []\ncomponents: []\nevidence: []\n")
        (pdir / "aces.yaml").write_bytes(text.encode("utf-8"))
        return pdir

    def use_fake_aces(self, mode: str = "new") -> Path:
        """Point ACES_ONSHAPE_ACES_CMD at tests/fake_aces.py; returns its call log."""
        log = self.tmp / "fake-aces.log"
        os.environ["ACES_ONSHAPE_ACES_CMD"] = json.dumps([sys.executable, str(FAKE_ACES)])
        os.environ["FAKE_ACES_MODE"] = mode
        os.environ["FAKE_ACES_LOG"] = str(log)
        return log

    def child_env(self, keys: bool = True, extra: dict | None = None) -> dict:
        return subprocess_env(self.tmp, fake=self.fake, keys=keys, extra=extra)

    def run_module(self, module: str, *argv: str, env: dict | None = None, input: bytes | None = None,
                   timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", module, *argv], env=env if env is not None else self.child_env(),
                              input=input, capture_output=True, timeout=timeout, cwd=str(self.tmp))

    def cli_main(self, *argv: str) -> tuple[int, str, str]:
        from aces_onshape import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def files_under(self, root: Path | None = None) -> list[Path]:
        root = root or self.tmp
        return [p for p in root.rglob("*") if p.is_file()]

    def assert_no_secrets_in_tree(self, root: Path | None = None) -> None:
        """No file under the temp dir contains the fake's keys or the base64 credential."""
        needles = [n.encode("utf-8") for n in (ACCESS, SECRET, basic(ACCESS, SECRET))]
        if self.fake is not None:
            needles += [n.encode("utf-8") for n in (self.fake.access, self.fake.secret,
                                                    basic(self.fake.access, self.fake.secret))]
        for path in self.files_under(root):
            data = path.read_bytes()
            for needle in needles:
                self.assertNotIn(needle, data, f"{path} contains a fixture credential")
