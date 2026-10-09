"""Tests 1-14 and 36-43 of the spec: the REST client, its retry and redirect rules, and the settings."""
from __future__ import annotations

import email.utils
import hashlib
import json
import os
import secrets
import socket
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fake_onshape
from fake_onshape import ACCESS, SECRET, SIGNATURE, AdapterTestCase

from aces_onshape import client as C
from aces_onshape import config
from aces_onshape.errors import (
    AdapterError,
    AuthError,
    ConfigError,
    NotFoundError,
    OnshapeError,
    RateLimitError,
    ServerError,
    TransportError,
    TranslationError,
    UsageError,
)

FEATURE = {"btType": "BTMFeature-134", "featureType": "fixtureFeature", "name": "Fixture feature", "namespace": "",
           "parameters": [{"btType": "BTMParameterQuantity-147", "parameterId": "depth", "expression": "5 mm",
                           "isInteger": False}], "suppressed": False}
SCRIPT = "function(context is Context, queries is map) { return 1; }"


def _write_env(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Recording:
    """assertRaises as a context manager that also keeps the exception, so tearDown can check its text."""

    def __init__(self, ctx, sink: list):
        self.ctx, self.sink = ctx, sink

    def __enter__(self):
        return self.ctx.__enter__()

    def __exit__(self, *exc):
        result = self.ctx.__exit__(*exc)
        if getattr(self.ctx, "exception", None) is not None:
            self.sink.append(self.ctx.exception)
        return result


class ClientTest(AdapterTestCase):
    """Test 13 holds for the whole file: tearDown checks str() of every exception any test here caught."""

    def setUp(self):
        super().setUp()
        self.raised: list[BaseException] = []

    def assertRaises(self, *args, **kwargs):
        ctx = super().assertRaises(*args, **kwargs)
        return _Recording(ctx, self.raised) if len(args) == 1 else ctx

    def tearDown(self):
        credential = fake_onshape.basic(ACCESS, SECRET)
        for e in self.raised:
            for needle in (ACCESS, SECRET, credential):
                self.assertNotIn(needle, str(e), f"{type(e).__name__} text contains a fixture credential")
        super().tearDown()

    def exact(self, method: str, suffix: str):
        return [r for r in self.fake.matching(method) if r.path.endswith(suffix)]

    def test_01_basic_header_on_every_request(self):
        c = self.client()
        c.session_info()
        doc = c.create_document("Header check (fixture)")
        c.elements(doc["id"], "w", doc["defaultWorkspace"]["id"])
        api = self.fake.matching(path_contains="/api/v17/")
        self.assertGreaterEqual(len(api), 3)
        for r in api:
            self.assertEqual(r.headers.get("authorization"), "Basic " + fake_onshape.basic(ACCESS, SECRET))
            self.assertEqual(r.headers.get("accept"), C.JSON_TYPE)
        self.assertEqual(json.loads(self.exact("POST", "/api/v17/documents")[0].body),
                         {"name": "Header check (fixture)", "isPublic": False})

    def test_02_auth_failures(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            opener.open(self.fake.base_url + "/users/sessioninfo", timeout=10)
        self.assertEqual(cm.exception.code, 401)
        cm.exception.close()
        wrong_secret = "WRONG-fixture-secret-0000"
        wrong = C.Client(self.fake.base_url, ACCESS, wrong_secret, allow_http_loopback=True, sleep=self.sleeps.append)
        with self.assertRaises(AuthError) as cm:
            wrong.session_info()
        e = cm.exception
        self.assertEqual(e.exit_code, 3)
        self.assertEqual(e.kind, "auth")
        self.assertIn("GET /users/sessioninfo", str(e))
        self.assertIn("(Onshape said: Unauthorized (fixture))", str(e))
        self.assertNotIn(wrong_secret, str(e))
        self.assertNotIn(SECRET, str(e))

    def test_03_validate_base_url(self):
        v = config.validate_base_url
        for bad in ("http://cad.onshape.com/api/v17", "https://user:pw@cad.onshape.com/api/v17",
                    "https://cad.onshape.com", "https://cad.onshape.com/api/v17?x=1",
                    "https://cad.onshape.com/api/v17\n"):
            with self.assertRaises(ConfigError, msg=bad) as cm:
                v(bad)
            self.assertNotIn("pw@", str(cm.exception))
        self.assertEqual(v("https://cad.onshape.com/api/v17/"), "https://cad.onshape.com/api/v17")
        self.assertEqual(v("https://acme.onshape.com/api/v17"), "https://acme.onshape.com/api/v17")
        with self.assertRaises(ConfigError):
            v("http://127.0.0.1:8080/api/v17")
        self.assertEqual(v("http://127.0.0.1:8080/api/v17", True), "http://127.0.0.1:8080/api/v17")
        with self.assertRaises(ConfigError):
            v("http://example.com/api/v17", True)
        env = {"ONSHAPE_ACCESS_KEY": ACCESS, "ONSHAPE_SECRET_KEY": SECRET, "ONSHAPE_BASE_URL": "https://cad.onshape.com/api"}
        with self.assertRaises(ConfigError) as cm:
            config.load_settings(env)
        self.assertEqual(cm.exception.exit_code, 2)

    def test_04_rate_limits(self):
        c = self.client()
        self.fake.inject(429, path_contains="/users/sessioninfo", headers={"Retry-After": "3"})
        c.session_info()
        self.assertEqual(self.sleeps, [3.0])
        self.assertEqual(self.fake.count("GET", "/users/sessioninfo"), 2)
        self.sleeps.clear()
        self.fake.inject(429, path_contains="/users/sessioninfo", count=6)
        with self.assertRaises(RateLimitError):
            c.session_info()
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0, 8.0, 16.0])
        self.sleeps.clear()
        self.fake.inject(429, path_contains="/users/sessioninfo", headers={"Retry-After": "3600"})
        with self.assertRaises(RateLimitError) as cm:
            c.session_info()
        self.assertIn("retry after 3600 s", str(cm.exception))
        self.assertEqual(self.sleeps, [])
        when = email.utils.format_datetime(datetime.now(timezone.utc) + timedelta(seconds=5), usegmt=True)
        self.fake.inject(429, path_contains="/users/sessioninfo", headers={"Retry-After": when})
        c.session_info()
        self.assertEqual(len(self.sleeps), 1)
        self.assertTrue(0.0 <= self.sleeps[0] <= 6.0, self.sleeps)

    def test_05_server_errors(self):
        c = self.client()
        did, wid, ps = self.fake.seed_document()
        self.fake.inject(503, path_contains="/elements")
        self.assertTrue(c.elements(did, "w", wid))
        self.assertEqual(self.sleeps, [1.0])
        self.fake.inject(500, method="POST", path_contains="/features")
        with self.assertRaises(ServerError) as cm:
            c.add_feature(did, wid, ps, FEATURE)
        self.assertIn("may or may not have been applied", str(cm.exception))
        self.assertEqual(self.fake.count("POST", "/features"), 1)
        self.assertEqual(self.sleeps, [1.0])

    def test_06_not_found(self):
        missing = secrets.token_hex(12)
        with self.assertRaises(NotFoundError) as cm:
            self.client().get_document(missing)
        self.assertIn(f"GET /documents/{missing}", str(cm.exception))
        self.assertIn("404", str(cm.exception))
        self.assertEqual(cm.exception.kind, "not_found")

    def test_07_feature_studio_round_trip(self):
        c = self.client()
        doc = c.create_document("Round trip (fixture)")
        did, wid = doc["id"], doc["defaultWorkspace"]["id"]
        eid = c.create_feature_studio(did, wid, "Studio (fixture)")["id"]
        code = fake_onshape.FIXTURE_HEADER + "\n// round trip (fixture)\n"
        c.update_feature_studio_contents(did, wid, eid, code)
        self.assertEqual(c.feature_studio_contents(did, "w", wid, eid)["contents"], code)
        self.assertEqual(json.loads(self.exact("POST", f"/featurestudios/d/{did}/w/{wid}")[0].body),
                         {"name": "Studio (fixture)"})
        self.assertEqual(json.loads(self.exact("POST", f"/featurestudios/d/{did}/w/{wid}/e/{eid}")[0].body),
                         {"btType": "BTFeatureStudioContents-2239", "contents": code})

    def test_08_eval_featurescript(self):
        did, wid, ps = self.fake.seed_document()
        c = self.client()
        first = c.eval_featurescript(did, "w", wid, ps, SCRIPT)
        self.assertEqual(self.fake.last_eval, {"btType": "BTFeatureScriptEvalCall-2377", "script": SCRIPT})
        self.assertNotIn("queries", self.fake.last_eval)
        second = c.eval_featurescript(did, "w", wid, ps, SCRIPT, {"q": ["JHD"]})
        self.assertEqual(self.fake.last_eval["queries"], {"q": ["JHD"]})
        self.assertEqual(first["result"], self.fake.eval_result)
        self.assertEqual(second["result"], self.fake.eval_result)

    def test_09_features_add_update_delete(self):
        did, wid, ps = self.fake.seed_document()
        c = self.client()
        r = c.add_feature(did, wid, ps, FEATURE)
        body = json.loads(self.fake.last("POST", "/features").body)
        self.assertEqual(body, {"btType": "BTFeatureDefinitionCall-1406", "feature": FEATURE})
        fid = r["feature"]["featureId"]
        stored = self.fake.feature(did, ps, fid)
        self.assertEqual({k: v for k, v in stored.items() if k != "featureId"}, FEATURE)
        c.update_feature(did, wid, ps, fid, dict(FEATURE, name="Renamed (fixture)"))
        self.assertEqual(json.loads(self.fake.last("POST", "/featureid/").body)["feature"]["featureId"], fid)
        self.assertEqual(self.fake.feature(did, ps, fid)["name"], "Renamed (fixture)")
        deleted = c.delete_feature(did, wid, ps, fid)
        self.assertIn("sourceMicroversion", deleted)
        self.assertIsNone(self.fake.feature(did, ps, fid))

    def test_10_create_version(self):
        did, wid, _ps = self.fake.seed_document()
        v = self.client().create_version(did, wid, "v1 (fixture)", "description (fixture)")
        self.assertEqual(json.loads(self.fake.last("POST", "/versions").body),
                         {"documentId": did, "workspaceId": wid, "name": "v1 (fixture)",
                          "description": "description (fixture)"})
        self.assertIn(v["id"], self.fake.versions(did))

    def test_11_export_step(self):
        did, wid, ps = self.fake.seed_document()
        c = self.client()
        dest = self.tmp / "part.step"
        r = c.export_step(did, "w", wid, ps, dest)
        self.assertEqual(json.loads(self.fake.last("POST", "/translations").body),
                         {"formatName": "STEP", "storeInDocument": False, "stepVersionString": "AP242"})
        tid = r["translation"]["id"]
        self.assertGreaterEqual(self.fake.count("GET", f"/translations/{tid}"), 2)
        self.assertEqual([s["state"] for s in r["translation"]["states"]], ["ACTIVE", "DONE"])
        self.assertEqual(r["translation"]["polls"], 2)
        data = dest.read_bytes()
        self.assertEqual(data, self.fake.step_bytes(tid))
        self.assertEqual(r["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(r["bytes"], len(data))
        self.fake.translation_mode = "fail"
        with self.assertRaises(TranslationError) as cm:
            c.export_step(did, "w", wid, ps, self.tmp / "failed.step")
        self.assertIn("fixture failure", str(cm.exception))
        self.assertFalse((self.tmp / "failed.step").exists())

    def test_12_stl_redirects(self):
        did, wid, ps = self.fake.seed_document()
        lines: list[str] = []
        c = self.client(trace=lines.append)
        same = self.tmp / "same.stl"
        c.export_stl(did, "w", wid, ps, same)
        self.assertEqual(self.fake.download_auth, [True])
        self.assertEqual(same.read_bytes(), fake_onshape.STL_FIXTURE)
        self.assertEqual(self.fake.last("GET", "/stl").query, {"units": "millimeter", "mode": "binary", "grouping": "true"})
        other = self.start_fake()
        self.fake.stl_redirect_origin = other.origin
        cross = self.tmp / "cross.stl"
        c.export_stl(did, "w", wid, ps, cross)
        self.assertEqual(other.download_auth, [False])
        self.assertEqual(cross.read_bytes(), fake_onshape.STL_FIXTURE)
        messages = []
        other.inject(404, path_contains="/fake-download/")
        with self.assertRaises(NotFoundError) as cm:
            c.export_stl(did, "w", wid, ps, self.tmp / "missing.stl")
        messages.append(str(cm.exception))
        self.assertIn("/fake-download/", str(cm.exception))
        self.fake.stl_redirect_origin = "http://download.example.invalid"
        with self.assertRaises(OnshapeError) as cm:
            c.export_stl(did, "w", wid, ps, self.tmp / "refused.stl")
        messages.append(str(cm.exception))
        self.assertIn("refusing to follow a redirect to http://download.example.invalid", str(cm.exception))
        self.assertTrue(any("/fake-download/" in line for line in lines))
        for text in lines + messages:
            self.assertNotIn(SIGNATURE, text)
            self.assertNotIn("sig=", text)

    def test_13_nothing_secret_in_reprs_and_errors(self):
        c = self.client()
        settings = config.load_settings()
        credential = fake_onshape.basic(ACCESS, SECRET)
        texts = [repr(c), repr(settings), repr(c.redactor)]
        wrong = C.Client(self.fake.base_url, ACCESS, "WRONG-fixture-secret-0000", allow_http_loopback=True,
                         sleep=self.sleeps.append)
        closed = C.Client(f"http://127.0.0.1:{_closed_port()}/api/v17", ACCESS, SECRET, allow_http_loopback=True,
                          sleep=self.sleeps.append, timeout=10.0)
        did, wid, ps = self.fake.seed_document()
        self.fake.inject(400, path_contains="/elements")
        attempts = [wrong.session_info, lambda: c.get_document(secrets.token_hex(12)),
                    lambda: c.elements(did, "w", wid), lambda: c.elements(did + "\n", "w", wid),
                    lambda: closed.create_document("Closed port (fixture)")]
        for attempt in attempts:
            with self.assertRaises(AdapterError) as cm:
                attempt()
            texts.append(str(cm.exception))
        for text in texts:
            for needle in (ACCESS, SECRET, credential):
                self.assertNotIn(needle, text)
        # the MCP server writes ASCII JSON lines: a value is also redacted in its \u-escaped form
        accented = "sécret-fixture-value-01"
        r = config.Redactor([accented])
        for ascii_only in (True, False):
            self.assertEqual(r.redact(json.dumps({"k": accented}, ensure_ascii=ascii_only)), '{"k": "[redacted]"}')

    def test_14_credentials(self):
        cfg = Path(os.environ["ACES_USER_CONFIG"])
        path = _write_env(cfg / "onshape.env", "ONSHAPE_ACCESS_KEY=FILE-fixture-access-01\n"
                                               "ONSHAPE_SECRET_KEY=FILE-fixture-secret-01\n")
        from_env = config.load_settings()
        self.assertEqual(from_env.source, "environment")
        self.assertEqual(from_env.access_key, self.fake.access)
        from_file = config.load_settings({"ACES_USER_CONFIG": str(cfg)})
        self.assertEqual(from_file.source, str(path))
        self.assertEqual(from_file.access_key, "FILE-fixture-access-01")
        self.assertEqual(from_file.secret_key, "FILE-fixture-secret-01")
        with self.assertRaises(AuthError) as cm:
            config.load_settings({"ONSHAPE_ACCESS_KEY": ACCESS, "ACES_USER_CONFIG": str(cfg)})
        self.assertIn("ONSHAPE_SECRET_KEY is not set", str(cm.exception))
        with self.assertRaises(AuthError) as cm:
            config.load_settings({"ACES_USER_CONFIG": str(self.tmp / "empty-config")})
        self.assertIn("onshape.env", str(cm.exception))
        self.assertIn("ONSHAPE_ACCESS_KEY", str(cm.exception))
        self.assertIn("no Onshape API keys", str(cm.exception))
        self.assertEqual(cm.exception.exit_code, 3)

    @unittest.skipUnless(os.name == "posix", "file modes are checked on POSIX only")
    def test_14b_credentials_file_mode(self):
        cfg = Path(os.environ["ACES_USER_CONFIG"])
        path = _write_env(cfg / "onshape.env", "ONSHAPE_ACCESS_KEY=FILE-fixture-access-01\n"
                                               "ONSHAPE_SECRET_KEY=FILE-fixture-secret-01\n")
        os.chmod(path, 0o644)
        with self.assertRaises(AuthError) as cm:
            config.load_settings({"ACES_USER_CONFIG": str(cfg)})
        self.assertIn("chmod 600", str(cm.exception))
        self.assertIn("mode 0644", str(cm.exception))

    def test_36_precedence_of_keys_and_base_url(self):
        cfg = self.tmp / "cfg36"
        keys = {"ONSHAPE_ACCESS_KEY": ACCESS, "ONSHAPE_SECRET_KEY": SECRET}
        _write_env(cfg / "onshape.env", "ONSHAPE_BASE_URL=http://not-allowed.example/api/v17\nthis line has no equals\n")
        s = config.load_settings({**keys, "ACES_USER_CONFIG": str(cfg)})                                     # (a)
        self.assertEqual((s.base_url, s.source, s.base_url_source), (config.DEFAULT_BASE_URL, "environment", "default"))
        s = config.load_settings({**keys, "ACES_USER_CONFIG": str(cfg),
                                  "ONSHAPE_BASE_URL": "https://env.onshape.com/api/v17"})                  # (b)
        self.assertEqual((s.base_url, s.base_url_source), ("https://env.onshape.com/api/v17", "environment"))
        path = _write_env(cfg / "onshape.env", "ONSHAPE_ACCESS_KEY=FILE-fixture-access-01\n"
                                               "ONSHAPE_SECRET_KEY=FILE-fixture-secret-01\n"
                                               "ONSHAPE_BASE_URL=https://file.onshape.com/api/v17\n")
        s = config.load_settings({"ACES_USER_CONFIG": str(cfg)})                                             # (c)
        self.assertEqual((s.base_url, s.source, s.base_url_source),
                         ("https://file.onshape.com/api/v17", str(path), str(path)))
        s = config.load_settings({"ACES_USER_CONFIG": str(cfg),
                                  "ONSHAPE_BASE_URL": "https://env.onshape.com/api/v17"})                  # (d)
        self.assertEqual((s.base_url, s.source, s.base_url_source),
                         ("https://env.onshape.com/api/v17", str(path), "environment"))
        _write_env(cfg / "onshape.env", "ONSHAPE_ACCESS_KEY=FILE-fixture-access-01\n"
                                        "ONSHAPE_SECRET_KEY=FILE-fixture-secret-01\n")
        s = config.load_settings({"ACES_USER_CONFIG": str(cfg)})                                             # (e)
        self.assertEqual((s.base_url, s.base_url_source), (config.DEFAULT_BASE_URL, "default"))

    def test_37_env_file_parsing(self):
        cfg = self.tmp / "cfg37"
        env = {"ACES_USER_CONFIG": str(cfg)}
        path = cfg / "onshape.env"
        _write_env(path, b"\xef\xbb\xbfONSHAPE_ACCESS_KEY=BOM-fixture-access-01\nONSHAPE_SECRET_KEY=BOM-fixture-secret-01\n")
        s = config.load_settings(env)
        self.assertEqual((s.access_key, s.secret_key), ("BOM-fixture-access-01", "BOM-fixture-secret-01"))
        _write_env(path, "# comment\n\nexport ONSHAPE_ACCESS_KEY='QUOTED-fixture-access-01'\n"
                         'ONSHAPE_SECRET_KEY="QUOTED-fixture-secret-01"\nOTHER_KEY=ignored\n')
        s = config.load_settings(env)
        self.assertEqual((s.access_key, s.secret_key), ("QUOTED-fixture-access-01", "QUOTED-fixture-secret-01"))
        _write_env(path, "ONSHAPE_ACCESS_KEY=FILE-fixture-access-01\nONSHAPE_SECRET_KEY=INLINE-fixture-value # note\n")
        with self.assertRaises(AuthError) as cm:
            config.load_settings(env)
        self.assertIn("inline comments", str(cm.exception))
        self.assertNotIn("INLINE-fixture-value", str(cm.exception))
        _write_env(path, "ONSHAPE_ACCESS_KEY=FILE-fixture-access-01\nONSHAPE_SECRET_KEY=\n")
        with self.assertRaises(AuthError) as cm:
            config.load_settings(env)
        self.assertIn("ONSHAPE_SECRET_KEY", str(cm.exception))
        self.assertIn("sets ONSHAPE_ACCESS_KEY but not ONSHAPE_SECRET_KEY", str(cm.exception))
        _write_env(path, "ONSHAPE_ACCESS_KEY=FILE-fixture-access-01\nONSHAPE_SECRET_KEY=FILE-fixture-secret-01\n")
        s = config.load_settings({**env, "ONSHAPE_ACCESS_KEY": "  ", "ONSHAPE_SECRET_KEY": ""})
        self.assertEqual(s.source, str(path))
        _write_env(path, "ONSHAPE_ACCESS_KEY=FILE-fixture-access-01\n# fine\nnot a key value line\n")
        with self.assertRaises(AuthError) as cm:
            config.load_settings(env)
        self.assertIn("line 3 is not KEY=value", str(cm.exception))
        self.assertNotIn("not a key value line", str(cm.exception))

    def test_38_trailing_newlines_and_unsendable_values(self):
        did, wid, ps = self.fake.seed_document()
        c = self.client()
        before = len(self.fake.requests)
        for call in (lambda: c.get_document(did + "\n"), lambda: c.elements(did + "\n", "w", wid),
                     lambda: c.elements(did, "w\n", wid), lambda: c.feature_studio_contents(did, "w", wid, ps + "\n"),
                     lambda: c.delete_feature(did, wid, ps, "F1_0\n"), lambda: c.get_translation("tid\n")):
            with self.assertRaises(UsageError):
                call()
        self.assertEqual(len(self.fake.requests), before)
        with self.assertRaises(UsageError) as cm:
            c.request_json("GET", "/documents/" + 24 * "a" + "\n")
        self.assertNotIsInstance(cm.exception, TransportError)
        self.assertIn("cannot be sent", str(cm.exception))
        self.assertEqual(self.sleeps, [])
        self.assertEqual(len(self.fake.requests), before)

    def test_39_may_forward_auth(self):
        f = C.may_forward_auth
        self.assertTrue(f("https://cad.onshape.com/api/v17", "https://cad.onshape.com/api/v17/x?sig=1"))
        self.assertTrue(f("https://cad.onshape.com/api/v17", "https://cad.onshape.com:443/y"))
        self.assertTrue(f("https://cad.onshape.com/api/v17", "https://files.onshape.com/x"))
        self.assertFalse(f("https://x.co.uk/api/v17", "https://y.co.uk/x"))
        self.assertFalse(f("https://cad.onshape.com/api/v17", "http://files.onshape.com/x"))
        self.assertFalse(f("https://cad.onshape.com/api/v17", "https://onshape.com.evil.example/x"))
        self.assertFalse(f("http://127.0.0.1:8000/api/v17", "http://127.0.0.1:8001/x"))

    def test_40_transport_classification(self):
        did, wid, ps = self.fake.seed_document()
        c = self.client(timeout=0.5)
        # (a) a write that may have reached Onshape is never retried
        self.fake.delay(2.0, method="POST", path_contains="/features")
        with self.assertRaises(TransportError) as cm:
            c.add_feature(did, wid, ps, FEATURE)
        self.assertTrue(cm.exception.maybe_applied)
        self.assertIn("may or may not have been applied", str(cm.exception))
        self.assertEqual(self.fake.count("POST", "/features"), 1)
        self.assertEqual(self.sleeps, [])
        # (b) a read is retried
        self.fake.delay(2.0, method="GET", path_contains="/elements", count=1)
        self.assertTrue(c.elements(did, "w", wid))
        self.assertEqual(self.sleeps, [1.0])
        # (c) nothing listening: not sent. The timeout is longer than 0.5 s here because Windows refuses a closed
        # loopback port only after about 2 s; at 0.5 s the connect times out instead (possibly sent).
        closed = C.Client(f"http://127.0.0.1:{_closed_port()}/api/v17", ACCESS, SECRET, allow_http_loopback=True,
                          sleep=self.sleeps.append, timeout=10.0)
        self.sleeps.clear()
        with self.assertRaises(TransportError) as cm:
            closed.create_document("Closed port (fixture)")
        self.assertFalse(cm.exception.maybe_applied)
        self.assertIn("cannot reach 127.0.0.1", str(cm.exception))
        self.assertNotIn("may or may not", str(cm.exception))
        self.assertEqual(self.sleeps, [])
        # (d) a DELETE applied but answered 503: the retry's 404 means it is gone
        fid = self.fake.seed_feature(did, wid, ps, FEATURE)
        self.fake.inject(503, method="DELETE", path_contains="/features/featureid/", apply=True)
        self.assertEqual(c.delete_feature(did, wid, ps, fid), {"already_deleted": True})
        self.assertIsNone(self.fake.feature(did, ps, fid))

    def test_40b_404_means_deleted_only_after_an_attempt_that_may_have_applied(self):
        did, wid, ps = self.fake.seed_document()
        c = self.client()
        # a 429 applied nothing, so the retry's 404 means there never was such a feature
        self.fake.inject(429, method="DELETE", path_contains="/features/featureid/", headers={"Retry-After": "0"})
        with self.assertRaises(NotFoundError):
            c.delete_feature(did, wid, ps, "FnoSuchFeature_0")
        self.assertEqual(self.fake.count("DELETE", "/features/featureid/"), 2)
        self.assertEqual(self.sleeps, [0.0])

        def scripted(*outcomes):
            pending = list(outcomes)

            def attempt():
                outcome = pending.pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
            return attempt

        gone = (404, {}, b'{"message": "Not found (fixture)"}', "/x", False)
        # never sent (connection refused), then 404: not found
        with self.assertRaises(NotFoundError):
            c._run("DELETE", "/x", True, scripted(C._Transport(False, ConnectionRefusedError()), gone), 1.0,
                   ok_404_after_retry=True)
        # sent with no response, then 404: the first attempt may have deleted it
        status, _hdrs, raw = c._run("DELETE", "/x", True, scripted(C._Transport(True, TimeoutError()), gone), 1.0,
                                    ok_404_after_retry=True)
        self.assertEqual((status, json.loads(raw)), (200, {"already_deleted": True}))

    def test_41_download_retry_rehashes(self):
        did, wid, ps = self.fake.seed_document()
        self.fake.inject(503, method="GET", path_contains="/externaldata/")
        dest = self.tmp / "retry.step"
        r = self.client().export_step(did, "w", wid, ps, dest)
        expected = self.fake.step_bytes(r["translation"]["id"])
        self.assertEqual(r["sha256"], hashlib.sha256(expected).hexdigest())
        self.assertEqual(r["bytes"], len(expected))
        self.assertEqual(dest.read_bytes(), expected)
        self.assertEqual(self.fake.count("GET", "/externaldata/"), 2)

    def test_42_forbidden_text(self):
        self.fake.inject(403, path_contains="/users/sessioninfo", body={"message": "API allocation exhausted (fixture)"})
        with self.assertRaises(AuthError) as cm:
            self.client().session_info()
        self.assertIn("Onshape refused this call: the API key may lack the scope for it, the account may have no "
                      "access to this document, or a plan or API-allocation limit may apply", str(cm.exception))
        self.assertIn("(Onshape said: API allocation exhausted (fixture))", str(cm.exception))

    def test_43_ids_in_responses_are_validated(self):
        did, wid, ps = self.fake.seed_document()
        c = self.client()
        self.fake.bad_translation_id = "../x"
        with self.assertRaises(OnshapeError) as cm:
            c.export_step(did, "w", wid, ps, self.tmp / "a.step")
        self.assertIn("unexpected", str(cm.exception))
        self.assertEqual(self.fake.count("GET", "/translations/"), 0)
        self.fake.bad_translation_id = None
        self.fake.bad_result_document_id = "nothex"
        with self.assertRaises(OnshapeError) as cm:
            c.export_step(did, "w", wid, ps, self.tmp / "b.step")
        self.assertIn("unexpected resultDocumentId", str(cm.exception))
        self.assertEqual(self.fake.count("GET", "/externaldata/"), 0)


if __name__ == "__main__":
    unittest.main()
