"""A standard-library client for the Onshape REST API (urllib, json, hashlib, base64; no requests).

What this module is verified against: the public Onshape OpenAPI description saved as openapi.json (OpenAPI 3.0.1,
"Onshape REST API" 1.221.89963-13d10d36cb23, servers https://cad.onshape.com/api/v17, saved 2026-10-09), for every
path, request body and response field used here, and the in-process fake server in tests/ (tests/fake_onshape.py).
It has NOT been run against a live Onshape account: there are no Onshape credentials on the machine it was written on.
Open until the live check in docs/PACKAGES.md is run:

- the custom-feature namespace forms (e<eid>::m<mid> and d<did>::v<vid>::e<eid>::m<mid>) are documented for /fstable
  custom tables, not for POST .../features, and whether the Part Studio featurespecs already give the right string;
- whether a FeatureScript compile error is visible anywhere on the studio write (the schema documents notices only
  on the eval response), and whether a REST studio write commits the way the UI's Commit does;
- which host the /stl 307 redirect points at and what auth it needs (Authorization is forwarded only to the base
  URL's own origin or between https *.onshape.com hosts), and whether `units` must be lower- or upper-case;
- the format of the eval `queries` map and what `console` holds;
- the formats of translation and external-data ids, the error-body shape, Retry-After on 429, and which status the
  plan and API-allocation limits use.

Calls made with API keys count against the account's annual API allocation.
"""
from __future__ import annotations

import email.utils
import hashlib
import http.client
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, config
from .errors import (
    AdapterError,
    AuthError,
    NotFoundError,
    OnshapeError,
    RateLimitError,
    ServerError,
    TransportError,
    TranslationError,
    UsageError,
)

JSON_TYPE = "application/json;charset=UTF-8; qs=0.09"
USER_AGENT = f"aces-cad-onshape/{__version__}"
CHUNK = 1 << 20
MAX_REDIRECTS = 5
REDIRECT_CODES = (301, 302, 303, 307, 308)
LOOPBACK_HOSTS = config.LOOPBACK_HOSTS
STEP_VERSION = "AP242"
STL_UNITS = ("millimeter", "centimeter", "meter", "inch", "foot", "yard")

ID_RE = r"[0-9a-fA-F]{24}"
FEATURE_ID_RE = r"[A-Za-z0-9_]{1,64}"
XID_RE = r"[A-Za-z0-9_.-]{1,128}"


# --- validation of ids (section 9.1; ops.py re-exports these) ----------------------------------------------------

def _shown(value) -> str:
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def validate_id(value, arg: str = "id") -> str:
    """A 24-hex Onshape id (document, workspace, version, element, microversion), lower-cased."""
    if not isinstance(value, str) or not re.fullmatch(ID_RE, value):
        raise UsageError(f"{arg} must be a 24-character hexadecimal Onshape id; got {_shown(value)}")
    return value.lower()


def validate_wvm(value, arg: str = "wvm", allowed: tuple[str, ...] = ("w", "v", "m")) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise UsageError(f"{arg} must be one of {', '.join(allowed)}; got {_shown(value)}")
    return value


def validate_feature_id(value, arg: str = "feature_id") -> str:
    if not isinstance(value, str) or not re.fullmatch(FEATURE_ID_RE, value):
        raise UsageError(f"{arg} must be an Onshape feature id (letters, digits and _, at most 64); "
                         f"got {_shown(value)}")
    return value


def validate_xid(value, arg: str = "id") -> str:
    """A translation id (tid) or external-data id (fid): the format is undocumented, so the rule is loose."""
    if not isinstance(value, str) or not re.fullmatch(XID_RE, value) or value in (".", ".."):
        raise UsageError(f"{arg} must be letters, digits, '_', '.' or '-' (at most 128); got {_shown(value)}")
    return value


def _response_xid(value, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(XID_RE, value) or value in (".", ".."):
        raise OnshapeError(f"Onshape returned an unexpected {field} format")
    return value


# --- URLs ---------------------------------------------------------------------------------------------------------

def _origin(url: str) -> tuple[str, str, int | None]:
    p = urllib.parse.urlsplit(url)
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    port = p.port or {"https": 443, "http": 80}.get(scheme)
    return scheme, host, port


def may_forward_auth(base_url: str, target_url: str) -> bool:
    """Whether a redirect target may receive the Authorization header.

    True only for the base URL's own origin (scheme, host, port), or when both URLs are https and both hosts end with
    ".onshape.com". Everything else (a pre-signed storage URL, a parent domain, plain http) is followed without it.
    """
    try:
        base = _origin(base_url)
        target = _origin(target_url)
    except ValueError:
        return False
    if not base[1] or not target[1]:
        return False
    if base == target:
        return True
    return (base[0] == "https" and target[0] == "https"
            and base[1].endswith(".onshape.com") and target[1].endswith(".onshape.com"))


def display_url(url: str) -> str:
    """scheme://host[:port]/path: never the query or the fragment, never userinfo."""
    try:
        p = urllib.parse.urlsplit(url)
        netloc = p.netloc.rpartition("@")[2]
        return f"{p.scheme}://{netloc}{p.path}"
    except ValueError:
        return "(unparseable URL)"


_URL_IN_TEXT = re.compile(r"""([A-Za-z][A-Za-z0-9+.-]*://[^\s?#'"<>]*)[?#][^\s'"<>]*""")


def _reason(exc: BaseException) -> str:
    """Exception class name and str(), at most 200 characters, with any URL query removed."""
    text = _URL_IN_TEXT.sub(r"\1", str(exc))
    return f"{type(exc).__name__}: {text[:200]}"


def _secs(value: float) -> str:
    return f"{value:g}"


def _detail(raw: bytes | None) -> str:
    if not raw or not raw.strip():
        return "(empty body)"
    text = raw.decode("utf-8", "replace")
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if isinstance(obj, dict) and isinstance(obj.get("message"), str):
        return " ".join(obj["message"].split())[:1000]
    return " ".join(text.split())[:300]


def _retry_after(headers, n: int) -> float:
    value = headers.get("Retry-After") if headers is not None else None
    if value:
        value = value.strip()
        if re.fullmatch(r"\d+", value):
            return float(int(value))
        try:
            when = email.utils.parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, IndexError, OverflowError):
            pass
    return float(2 ** (n - 1))


def utc_z(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Every 3xx surfaces as HTTPError; the client decides where (and with which headers) to follow."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


class _Unsendable(Exception):
    """A value cannot be put on the wire (control characters in the path, a header value urllib refuses)."""


class _Transport(Exception):
    def __init__(self, sent: bool, exc: BaseException):
        super().__init__(type(exc).__name__)
        self.sent = sent
        self.exc = exc
        self.where = ""
        self.host = ""


class Client:
    """The Onshape REST client. Construction sends nothing; every endpoint method is one documented REST call."""

    def __init__(self, base_url: str, access_key: str, secret_key: str, *, timeout: float = 30.0,
                 download_timeout: float = 300.0, max_retries_429: int = 5, max_retries_5xx: int = 3,
                 max_retry_wait: float = 60.0, allow_http_loopback: bool = False,
                 sleep=time.sleep, trace=None, credentials_path: str = ""):
        self.base_url = config.validate_base_url(base_url, allow_http_loopback)
        self.base_host = (urllib.parse.urlsplit(self.base_url).hostname or "").lower()
        credential = config.basic_credential(access_key, secret_key)
        self._auth = "Basic " + credential
        self._redactor = config.Redactor([access_key, secret_key, credential])
        self.timeout = float(timeout)
        self.download_timeout = float(download_timeout)
        self.max_retries_429 = int(max_retries_429)
        self.max_retries_5xx = int(max_retries_5xx)
        self.max_retry_wait = float(max_retry_wait)
        self._allow_http_loopback = bool(allow_http_loopback)
        self._sleep = sleep
        self._trace = trace
        self.credentials_path = credentials_path or str(config.credentials_path())
        self.credentials_source = "arguments"
        handlers: list = [_NoRedirect()]
        if self.base_host in LOOPBACK_HOSTS:
            handlers.append(urllib.request.ProxyHandler({}))  # no proxy, ever, for a loopback base URL
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        self._opener = urllib.request.build_opener(*handlers)

    @classmethod
    def from_settings(cls, s: config.Settings, **kw) -> "Client":
        kw.setdefault("allow_http_loopback", s.allow_http_loopback)
        kw.setdefault("credentials_path", s.source if s.source != "environment" else str(config.credentials_path()))
        client = cls(s.base_url, s.access_key, s.secret_key, **kw)
        client.credentials_source = s.source
        return client

    def __repr__(self) -> str:
        return f"Client(base_url={self.base_url!r})"

    def redact(self, text: str) -> str:
        return self._redactor.redact(text)

    def redact_obj(self, obj) -> tuple[object, bool]:
        return self._redactor.redact_obj(obj)

    @property
    def redactor(self) -> config.Redactor:
        return self._redactor

    # --- mechanics ------------------------------------------------------------------------------------------------

    def _emit(self, line: str) -> None:
        if self._trace is not None:
            self._trace(self.redact(line))

    @staticmethod
    def _api_path(path: str, query: dict | None, method: str = "GET") -> str:
        """The path under the base URL with its query string. A path character outside printable ASCII is refused
        here, because urllib.request strips leading and trailing whitespace from a URL instead of refusing it."""
        if not isinstance(path, str) or re.search(r"[^\x21-\x7e]", path):
            shown = str(path).encode("unicode_escape").decode("ascii")
            raise UsageError(f"invalid request {method} {shown}: a value contains characters that cannot be sent")
        if not query:
            return path
        pairs = []
        for key, value in query.items():
            if value is None:
                continue
            if isinstance(value, bool):
                value = "true" if value else "false"
            pairs.append((key, str(value)))
        return path + ("?" + urllib.parse.urlencode(pairs) if pairs else "")

    def _exchange(self, method: str, url: str, where: str, headers: dict, data: bytes | None, timeout: float,
                  sink=None):
        """One HTTP exchange: (status, headers, body). A 2xx body goes to `sink` when one is given."""
        t0 = time.monotonic()
        try:
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
            except ValueError:
                raise _Unsendable() from None
            try:
                resp = self._opener.open(req, timeout=timeout)
            except urllib.error.HTTPError as e:
                try:
                    body = e.read()
                except Exception:  # noqa: BLE001 (an unreadable error body is reported as empty)
                    body = b""
                finally:
                    e.close()
                status, hdrs = e.code, e.headers
            except urllib.error.URLError as e:
                reason = e.reason if isinstance(e.reason, BaseException) else e
                not_sent = isinstance(reason, (socket.gaierror, ConnectionRefusedError, ssl.SSLError))
                raise _Transport(not not_sent, reason) from None
            except http.client.InvalidURL:
                raise _Unsendable() from None
            except ValueError:
                raise _Unsendable() from None
            except (TimeoutError, ConnectionError, http.client.HTTPException, OSError) as e:
                raise _Transport(True, e) from None
            else:
                try:
                    with resp:
                        status, hdrs = resp.status, resp.headers
                        if sink is None:
                            body = resp.read()
                        else:
                            body = None
                            while True:
                                chunk = resp.read(CHUNK)
                                if not chunk:
                                    break
                                sink(chunk)
                except (TimeoutError, ConnectionError, http.client.HTTPException, OSError) as e:
                    raise _Transport(True, e) from None
        except _Transport as t:
            t.where = where
            t.host = (urllib.parse.urlsplit(url).hostname or "").lower()
            self._emit(f"{method} {where} -> {type(t.exc).__name__} ({int((time.monotonic() - t0) * 1000)} ms)")
            raise
        self._emit(f"{method} {where} -> {status} ({int((time.monotonic() - t0) * 1000)} ms)")
        return status, hdrs, body

    def _http_error(self, method: str, where: str, status: int, detail: str, hop: bool) -> OnshapeError:
        head = f"{method} {where} -> HTTP {status}"
        kw = {"method": method, "path": where, "status": status}
        if status in (401, 403) and hop:
            return OnshapeError(f"{head}: the download host refused the redirected request, which carries no "
                                f"Onshape credentials ({detail})", **kw)
        if status == 401:
            return AuthError(f"{head}: Onshape rejected the API key; check ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY "
                             f"or {self.credentials_path} (Onshape said: {detail})", **kw)
        if status == 403:
            return AuthError(f"{head}: Onshape refused this call: the API key may lack the scope for it, the account "
                             f"may have no access to this document, or a plan or API-allocation limit may apply "
                             f"(Onshape said: {detail})", **kw)
        if status == 404:
            return NotFoundError(f"{head}: {detail}", **kw)
        return OnshapeError(f"{head}: {detail}", **kw)

    def _run(self, method: str, api: str, idempotent: bool, attempt, timeout: float, *,
             ok_404_after_retry: bool = False):
        """The retry policy around `attempt()`, which returns (status, headers, body, where, hop).

        `ok_404_after_retry` (DELETE): a 404 counts as "already deleted" only after a retry of an attempt that may have
        been applied (sent with no response, or answered 5xx); after a 429 or a request that was never sent, the
        resource was not touched by this call, so its 404 is a NotFoundError.
        """
        attempts = n429 = nfail = 0
        maybe_applied = False
        while True:
            attempts += 1
            try:
                status, hdrs, raw, where, hop = attempt()
            except _Unsendable:
                raise UsageError(f"invalid request {method} {api}: a value contains characters that cannot be "
                                 f"sent") from None
            except _Transport as t:
                where = t.where or api
                host = t.host or self.base_host
                reason = _reason(t.exc)
                kw = {"method": method, "path": where}
                if not t.sent:
                    if idempotent and nfail < self.max_retries_5xx:
                        nfail += 1
                        self._sleep(float(2 ** (nfail - 1)))
                        continue
                    raise TransportError(f"cannot reach {host} ({method} {where}): {reason}", maybe_applied=False,
                                         **kw) from None
                if not idempotent:
                    raise TransportError(f"no response to {method} {where} within {_secs(timeout)} s; the request "
                                         f"may or may not have been applied: check the document before retrying "
                                         f"({reason})", maybe_applied=True, **kw) from None
                if nfail < self.max_retries_5xx:
                    nfail += 1
                    maybe_applied = True
                    self._sleep(float(2 ** (nfail - 1)))
                    continue
                raise TransportError(f"no response from {host} to {method} {where} within {_secs(timeout)} s after "
                                     f"{attempts} attempts: {reason}", maybe_applied=False, **kw) from None
            if 200 <= status < 300:
                return status, hdrs, raw
            kw = {"method": method, "path": where, "status": status}
            if 300 <= status < 400:
                location = hdrs.get("Location") if hdrs is not None else None
                shown = display_url(urllib.parse.urljoin(self.base_url + api, location)) if location else "(no Location)"
                raise OnshapeError(f"{method} {where} -> HTTP {status}: unexpected redirect to {shown}", **kw)
            detail = _detail(raw)
            if status == 404 and ok_404_after_retry and maybe_applied:
                return 200, hdrs, b'{"already_deleted": true}'
            if status == 429:
                n429 += 1
                if n429 > self.max_retries_429:
                    raise RateLimitError(f"{method} {where} -> HTTP 429: {detail}; still rate limited after "
                                         f"{self.max_retries_429} retries", **kw)
                wait = _retry_after(hdrs, n429)
                if wait > self.max_retry_wait:
                    raise RateLimitError(f"{method} {where} -> HTTP 429: Onshape asked to retry after {_secs(wait)} s "
                                         f"(rate limit); not waiting longer than {_secs(self.max_retry_wait)} s", **kw)
                self._sleep(wait)
                continue
            if status >= 500:
                if not idempotent:
                    raise ServerError(f"{method} {where} -> HTTP {status}: {detail}; the request may or may not have "
                                      f"been applied: check the document before retrying", **kw)
                if nfail < self.max_retries_5xx:
                    nfail += 1
                    maybe_applied = True
                    self._sleep(float(2 ** (nfail - 1)))
                    continue
                raise ServerError(f"{method} {where} -> HTTP {status}: {detail}", **kw)
            raise self._http_error(method, where, status, detail, hop)

    def request_json(self, method: str, path: str, *, query: dict | None = None, body=None,
                     idempotent: bool | None = None, ok_404_after_retry: bool = False):
        """One REST call with JSON in and out, under the retry rules of section 6.2."""
        method = method.upper()
        if idempotent is None:
            idempotent = method in ("GET", "DELETE", "HEAD")
        api = self._api_path(path, query, method)
        url = self.base_url + api
        headers = {"Accept": JSON_TYPE, "User-Agent": USER_AGENT, "Authorization": self._auth}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = JSON_TYPE

        def attempt():
            status, hdrs, raw = self._exchange(method, url, api, headers, data, self.timeout)
            return status, hdrs, raw, api, False

        status, _hdrs, raw = self._run(method, api, idempotent, attempt, self.timeout,
                                       ok_404_after_retry=ok_404_after_retry)
        if not raw or not raw.strip():
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise OnshapeError(f"{method} {api} -> HTTP {status}: response is not JSON", method=method, path=api,
                               status=status) from None

    def _check_redirect_target(self, target: str) -> None:
        p = urllib.parse.urlsplit(target)
        scheme = p.scheme.lower()
        host = (p.hostname or "").lower()
        if scheme == "https" and host:
            return
        if scheme == "http" and self._allow_http_loopback and host in LOOPBACK_HOSTS:
            return
        raise OnshapeError(f"refusing to follow a redirect to {scheme}://{host}")

    def download(self, path: str, dest: Path, *, query: dict | None = None) -> dict:
        """GET a file to `dest`, following redirects by hand; returns {"sha256", "bytes"} of the bytes on disk."""
        api = self._api_path(path, query)
        dest = Path(dest)
        box: dict = {}

        def follow(sink):
            url = self.base_url + api
            where, hop = api, False
            for _ in range(MAX_REDIRECTS + 1):
                headers = {"Accept": "*/*", "User-Agent": USER_AGENT}
                if may_forward_auth(self.base_url, url):
                    headers["Authorization"] = self._auth
                status, hdrs, raw = self._exchange("GET", url, where, headers, None, self.download_timeout, sink)
                location = hdrs.get("Location") if hdrs is not None else None
                if status in REDIRECT_CODES and location:
                    target = urllib.parse.urljoin(url, location)
                    self._check_redirect_target(target)
                    url, where, hop = target, display_url(target), True
                    continue
                return status, hdrs, raw, where, hop
            raise OnshapeError(f"GET {api}: more than {MAX_REDIRECTS} redirects", method="GET", path=api)

        def attempt():
            box["hash"] = hashlib.sha256()
            box["bytes"] = 0
            try:
                fh = open(dest, "wb")
            except OSError as e:
                raise AdapterError(f"cannot write {dest}: {e.strerror or type(e).__name__}") from None
            with fh:
                def sink(chunk: bytes) -> None:
                    try:
                        fh.write(chunk)
                    except OSError as e:
                        raise AdapterError(f"cannot write {dest}: {e.strerror or type(e).__name__}") from None
                    box["hash"].update(chunk)
                    box["bytes"] += len(chunk)
                return follow(sink)

        try:
            self._run("GET", api, True, attempt, self.download_timeout)
            on_disk, size = _hash_file(dest)
        except BaseException:
            try:
                dest.unlink()
            except OSError:
                pass
            raise
        if on_disk != box["hash"].hexdigest() or size != box["bytes"]:
            raise AdapterError(f"download of {api} wrote {box['bytes']} bytes but {size} are on disk")
        return {"sha256": on_disk, "bytes": size}

    # --- endpoints (section 6.4) ----------------------------------------------------------------------------------

    def session_info(self) -> dict:
        return self.request_json("GET", "/users/sessioninfo")

    def list_documents(self, q: str = "", limit: int = 20, offset: int = 0) -> dict:
        limit = max(1, min(20, int(limit)))
        return self.request_json("GET", "/documents", query={"q": q, "limit": limit, "offset": int(offset)})

    def create_document(self, name: str, description: str | None = None) -> dict:
        body = {"name": name, "isPublic": False}
        if description is not None:
            body["description"] = description
        return self.request_json("POST", "/documents", body=body, idempotent=False)

    def get_document(self, did: str) -> dict:
        did = validate_id(did, "did")
        return self.request_json("GET", f"/documents/{did}")

    def elements(self, did: str, wvm: str, wvmid: str):
        did, wvm, wvmid = validate_id(did, "did"), validate_wvm(wvm), validate_id(wvmid, "wvmid")
        return self.request_json("GET", f"/documents/d/{did}/{wvm}/{wvmid}/elements")

    def current_microversion(self, did: str, wv: str, wvid: str) -> dict:
        did, wv, wvid = validate_id(did, "did"), validate_wvm(wv, "wv", ("w", "v")), validate_id(wvid, "wvid")
        return self.request_json("GET", f"/documents/d/{did}/{wv}/{wvid}/currentmicroversion")

    def create_feature_studio(self, did: str, wid: str, name: str) -> dict:
        did, wid = validate_id(did, "did"), validate_id(wid, "wid")
        return self.request_json("POST", f"/featurestudios/d/{did}/w/{wid}", body={"name": name}, idempotent=False)

    def feature_studio_contents(self, did: str, wvm: str, wvmid: str, eid: str) -> dict:
        did, wvm, wvmid, eid = self._dwe(did, wvm, wvmid, eid)
        return self.request_json("GET", f"/featurestudios/d/{did}/{wvm}/{wvmid}/e/{eid}")

    def update_feature_studio_contents(self, did: str, wid: str, eid: str, contents: str) -> dict:
        did, wid, eid = validate_id(did, "did"), validate_id(wid, "wid"), validate_id(eid, "eid")
        body = {"btType": "BTFeatureStudioContents-2239", "contents": contents}
        return self.request_json("POST", f"/featurestudios/d/{did}/w/{wid}/e/{eid}", body=body, idempotent=True)

    def feature_studio_specs(self, did: str, wvm: str, wvmid: str, eid: str) -> dict:
        did, wvm, wvmid, eid = self._dwe(did, wvm, wvmid, eid)
        return self.request_json("GET", f"/featurestudios/d/{did}/{wvm}/{wvmid}/e/{eid}/featurespecs")

    def part_studio_specs(self, did: str, wvm: str, wvmid: str, eid: str) -> dict:
        did, wvm, wvmid, eid = self._dwe(did, wvm, wvmid, eid)
        return self.request_json("GET", f"/partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/featurespecs")

    def eval_featurescript(self, did: str, wvm: str, wvmid: str, eid: str, script: str,
                           queries: dict | None = None) -> dict:
        did, wvm, wvmid, eid = self._dwe(did, wvm, wvmid, eid)
        body = {"btType": "BTFeatureScriptEvalCall-2377", "script": script}
        if queries is not None:
            body["queries"] = queries
        return self.request_json("POST", f"/partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/featurescript", body=body,
                                 idempotent=True)

    def part_studio_features(self, did: str, wvm: str, wvmid: str, eid: str) -> dict:
        did, wvm, wvmid, eid = self._dwe(did, wvm, wvmid, eid)
        return self.request_json("GET", f"/partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/features")

    def add_feature(self, did: str, wid: str, eid: str, feature: dict) -> dict:
        did, wid, eid = validate_id(did, "did"), validate_id(wid, "wid"), validate_id(eid, "eid")
        body = {"btType": "BTFeatureDefinitionCall-1406", "feature": feature}
        return self.request_json("POST", f"/partstudios/d/{did}/w/{wid}/e/{eid}/features", body=body,
                                 idempotent=False)

    def update_feature(self, did: str, wid: str, eid: str, fid: str, feature: dict) -> dict:
        did, wid, eid = validate_id(did, "did"), validate_id(wid, "wid"), validate_id(eid, "eid")
        fid = validate_feature_id(fid, "feature_id")
        body = {"btType": "BTFeatureDefinitionCall-1406", "feature": {**feature, "featureId": fid}}
        return self.request_json("POST", f"/partstudios/d/{did}/w/{wid}/e/{eid}/features/featureid/{_q(fid)}",
                                 body=body, idempotent=True)

    def delete_feature(self, did: str, wid: str, eid: str, fid: str) -> dict:
        did, wid, eid = validate_id(did, "did"), validate_id(wid, "wid"), validate_id(eid, "eid")
        fid = validate_feature_id(fid, "feature_id")
        return self.request_json("DELETE", f"/partstudios/d/{did}/w/{wid}/e/{eid}/features/featureid/{_q(fid)}",
                                 idempotent=True, ok_404_after_retry=True)

    def create_version(self, did: str, wid: str, name: str, description: str = "") -> dict:
        did, wid = validate_id(did, "did"), validate_id(wid, "wid")
        body = {"documentId": did, "workspaceId": wid, "name": name, "description": description}
        return self.request_json("POST", f"/documents/d/{did}/versions", body=body, idempotent=False)

    def mass_properties(self, did: str, wvm: str, wvmid: str, eid: str) -> dict:
        did, wvm, wvmid, eid = self._dwe(did, wvm, wvmid, eid)
        return self.request_json("GET", f"/partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/massproperties",
                                 query={"massAsGroup": True})

    def export_stl(self, did: str, wvm: str, wvmid: str, eid: str, dest: Path, units: str = "millimeter",
                   mode: str = "binary", grouping: bool = True) -> dict:
        did, wvm, wvmid, eid = self._dwe(did, wvm, wvmid, eid)
        if units not in STL_UNITS:
            raise UsageError(f"units must be one of {', '.join(STL_UNITS)}; got {_shown(units)}")
        if mode not in ("binary", "text"):
            raise UsageError(f"mode must be binary or text; got {_shown(mode)}")
        return self.download(f"/partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/stl", dest,
                             query={"units": units, "mode": mode, "grouping": bool(grouping)})

    def start_translation(self, did: str, wv: str, wvid: str, eid: str) -> dict:
        did, wv, wvid = validate_id(did, "did"), validate_wvm(wv, "wv", ("w", "v")), validate_id(wvid, "wvid")
        eid = validate_id(eid, "eid")
        body = {"formatName": "STEP", "storeInDocument": False, "stepVersionString": STEP_VERSION}
        return self.request_json("POST", f"/partstudios/d/{did}/{wv}/{wvid}/e/{eid}/translations", body=body,
                                 idempotent=True)

    def get_translation(self, tid: str) -> dict:
        tid = validate_xid(tid, "tid")
        return self.request_json("GET", f"/translations/{_q(tid)}")

    def download_external_data(self, did: str, fid: str, dest: Path) -> dict:
        did, fid = validate_id(did, "did"), validate_xid(fid, "fid")
        return self.download(f"/documents/d/{did}/externaldata/{_q(fid)}", dest)

    @staticmethod
    def _dwe(did, wvm, wvmid, eid) -> tuple[str, str, str, str]:
        return validate_id(did, "did"), validate_wvm(wvm), validate_id(wvmid, "wvmid"), validate_id(eid, "eid")

    # --- translations ---------------------------------------------------------------------------------------------

    def wait_translation(self, tid: str, first_state: str | None, *, initial: float,
                         timeout: float) -> tuple[dict, list[dict]]:
        """Poll GET /translations/{tid} with backoff (initial, x1.5, at most 10 s) until DONE.

        States are recorded whenever they change, entry 0 being the POST response's; the last entry carries the
        total poll count. FAILED and the timeout raise TranslationError.
        """
        tid = validate_xid(tid, "tid")
        states = [{"state": first_state, "at": utc_z(), "poll": 0}]
        last = first_state
        interval = max(0.01, float(initial))
        waited = 0.0
        polls = 0
        t0 = time.monotonic()
        while True:
            self._sleep(interval)
            waited += interval
            info = self.get_translation(tid)
            polls += 1
            state = info.get("requestState") if isinstance(info, dict) else None
            if state != last:
                states.append({"state": state, "at": utc_z(), "poll": polls})
                last = state
            if state == "DONE":
                if states[-1]["poll"] != polls:  # DONE already in the POST response: close the record at this poll
                    states.append({"state": state, "at": utc_z(), "poll": polls})
                return info, states
            if state == "FAILED":
                reason = info.get("failureReason") or "no reason given"
                raise TranslationError(f"STEP translation {tid} FAILED: {reason}")
            elapsed = max(time.monotonic() - t0, waited)
            if elapsed >= timeout:
                raise TranslationError(f"STEP translation {tid} still {last or 'ACTIVE'} after {_secs(round(elapsed))} s")
            interval = min(interval * 1.5, 10.0)

    def export_step(self, did: str, wv: str, wvid: str, eid: str, dest: Path, *,
                    poll: tuple[float, float] | None = None) -> dict:
        """Start a STEP AP242 translation (not stored in the document), wait for it, download the file."""
        initial, timeout = poll or config.poll_settings()
        started = self.start_translation(did, wv, wvid, eid)
        tid = _response_xid(started.get("id") if isinstance(started, dict) else None, "translation id")
        info, states = self.wait_translation(tid, started.get("requestState"), initial=initial, timeout=timeout)
        result_did = info.get("resultDocumentId") or None
        if result_did is not None and (not isinstance(result_did, str) or not re.fullmatch(ID_RE, result_did)):
            raise OnshapeError("Onshape returned an unexpected resultDocumentId format")
        ids = info.get("resultExternalDataIds") or []
        if not ids:
            raise TranslationError(f"STEP translation {tid} is DONE but Onshape returned no resultExternalDataIds")
        fid = _response_xid(ids[0], "resultExternalDataIds")
        data_did = result_did.lower() if result_did else validate_id(did, "did")
        got = self.download_external_data(data_did, fid, dest)
        return {
            "sha256": got["sha256"],
            "bytes": got["bytes"],
            "translation": {
                "id": tid,
                "format": "STEP",
                "step_version": STEP_VERSION,
                "request_state": info.get("requestState"),
                "states": states,
                "polls": states[-1]["poll"],
                "result_external_data_ids": list(ids),
                "result_document_id": result_did,
            },
        }
