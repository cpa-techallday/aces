"""Tests 15-18, 44 and 45 of the spec: FeatureScript helpers, namespaces, references and the validators."""
from __future__ import annotations

import subprocess
import sys
import unittest

import fake_onshape
from fake_onshape import FIXTURE_HEADER, AdapterTestCase

from aces_onshape import client as C
from aces_onshape import featurescript as fs
from aces_onshape import ops
from aces_onshape import project as aces_project
from aces_onshape.errors import ProjectError, UsageError

D, V, E, M = "d" * 24, "1" * 24, "e" * 24, "a" * 24
BODY = ('annotation { "Feature Type Name" : "ACES plate" }\n'
        "export const acesPlate = defineFeature(function(context is Context, id is Id, definition is map)\n"
        "    precondition { isLength(definition.length, NONNEGATIVE_LENGTH_BOUNDS); } { });\n")
SPEC = {"btType": "BTFeatureSpec-129", "featureType": "acesPlate", "featureTypeName": "ACES plate", "namespace": "",
        "parameters": [
            {"btType": "BTParameterSpecQuantity-173", "parameterId": "length", "quantityType": "LENGTH",
             "defaultValue": {"btType": "BTMParameterQuantity-147", "parameterId": "length", "expression": "1 mm",
                              "isInteger": False}},
            {"btType": "BTParameterSpecQuantity-173", "parameterId": "count", "quantityType": "INTEGER",
             "defaultValue": {"btType": "BTMParameterQuantity-147", "parameterId": "count", "expression": "1",
                              "isInteger": True}},
            {"btType": "BTParameterSpecBoolean-170", "parameterId": "flag",
             "defaultValue": {"btType": "BTMParameterBoolean-144", "parameterId": "flag", "value": False}},
            {"btType": "BTParameterSpecString-175", "parameterId": "label",
             "defaultValue": {"btType": "BTMParameterString-149", "parameterId": "label", "value": ""}},
            {"btType": "BTParameterSpecQuery-174", "parameterId": "faces"},
            {"btType": "BTParameterSpecEnum-171", "parameterId": "corner", "enumName": "PlateCorner", "namespace": "",
             "options": ["SQUARE", "ROUND"],
             "defaultValue": {"btType": "BTMParameterEnum-145", "parameterId": "corner", "enumName": "PlateCorner",
                              "value": "SQUARE", "namespace": ""}},
            {"btType": "BTParameterSpecEnum-171", "parameterId": "bound", "enumName": "BoundingType", "namespace": "",
             "options": ["BLIND", "THROUGH_ALL"]},
            {"btType": "BTParameterSpecAppearance-1740", "parameterId": "look"},
        ]}


def by_id(params: list) -> dict:
    return {p["parameterId"]: p for p in params}


class FeatureScriptTest(AdapterTestCase):
    START_FAKE = False

    def child_seconds(self, code: str) -> float:
        """Run `code` in a child Python and return the seconds it printed last. A scan that backtracks without end
        then fails at the timeout instead of stalling the whole run (re holds the GIL, so a thread cannot time it)."""
        p = subprocess.run([sys.executable, "-c", code], env=fake_onshape.subprocess_env(self.tmp),
                           capture_output=True, timeout=60, cwd=str(self.tmp))
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        return float(p.stdout.decode("ascii").split()[-1])

    def test_15_headers(self):
        header = fs.parse_header(FIXTURE_HEADER)
        self.assertEqual(header, {"language_version": 2000,
                                  "std_imports": [{"path": "onshape/std/common.fs", "version": "2000.0"}]})
        commented = ("// a comment\n/* a block\n comment */\n  FeatureScript 1890;\n"
                     'import(path : "onshape/std/geometry.fs", version : "1890.0");\n')
        self.assertEqual(fs.parse_header(commented)["language_version"], 1890)
        self.assertEqual(fs.header_lines(commented), ["  FeatureScript 1890;",
                                                      'import(path : "onshape/std/geometry.fs", version : "1890.0");'])
        written, prepended, warnings = fs.ensure_header(BODY, FIXTURE_HEADER)
        self.assertTrue(prepended)
        self.assertEqual(written, FIXTURE_HEADER + "\n" + BODY)
        self.assertEqual(warnings, [])
        own = 'FeatureScript 2000;\nimport(path : "onshape/std/geometry.fs", version : "2000.0");\n' + BODY
        self.assertEqual(fs.ensure_header(own, FIXTURE_HEADER), (own, False, []))
        older = 'FeatureScript 1890;\nimport(path : "onshape/std/geometry.fs", version : "1890.0");\n' + BODY
        _w, _p, warnings = fs.ensure_header(older, FIXTURE_HEADER)
        self.assertEqual(warnings, ["code declares FeatureScript 1890; the studio's header declared 2000"])
        _w, _p, warnings = fs.ensure_header("FeatureScript 2000;\n" + BODY, FIXTURE_HEADER)
        self.assertEqual(warnings, ["no import of onshape/std/...: standard library functions will not resolve"])
        written, prepended, warnings = fs.ensure_header(BODY, "")
        self.assertEqual((written, prepended), (BODY, False))
        self.assertEqual(warnings, ["the studio has no FeatureScript header to copy; the code was written as given"])
        crlf = FIXTURE_HEADER.replace("\n", "\r\n")
        self.assertEqual(fs.header_lines(crlf), FIXTURE_HEADER.splitlines())

    def test_15b_header_scan_is_linear(self):
        # a block comment ends at its first */: an unclosed one is not a comment, and code between comments is code
        self.assertIsNone(fs.parse_header("/**//*/FeatureScript 7;")["language_version"])
        self.assertIsNone(fs.parse_header("/* a */ x = 1; /* b */ FeatureScript 5;")["language_version"])
        self.assertEqual(fs.parse_header("/* a */\n/** b **/ /***/\n// c\nFeatureScript 2384;")["language_version"],
                         2384)
        # 40 leading comments and no header took the lazy /\*.*?\*/ pattern 2^39 tries
        seconds = self.child_seconds(
            "import time\n"
            "from aces_onshape import featurescript as fs\n"
            "text = '/* x */\\n' * 40 + 'annotation {}'\n"
            "t0 = time.perf_counter()\n"
            "assert fs.ensure_header(text, '')[:2] == (text, False)\n"
            "assert fs.parse_header(text)['language_version'] is None and fs.header_lines(text) == []\n"
            "print(time.perf_counter() - t0)\n")
        self.assertLess(seconds, 1.0)

    def test_16_decode_fsvalue(self):
        d = fs.decode_fsvalue
        self.assertEqual(d({"btType": "BTFSValueNumber-772", "value": 2.5}), 2.5)
        self.assertEqual(d({"btType": "BTFSValueWithUnits-1817", "value": 0.06, "unitToPower": {"METER": 1}}),
                         {"value": 0.06, "units": {"METER": 1}})
        self.assertEqual(d({"btType": "BTFSValueString-1422", "value": "x"}), "x")
        self.assertIs(d({"btType": "BTFSValueBoolean-1195", "value": True}), True)
        self.assertEqual(d({"btType": "BTFSValueArray-1499", "value": [{"btType": "BTFSValueNumber-772", "value": 1},
                                                                       {"btType": "BTFSValueString-1422", "value": "a"}]}),
                         [1, "a"])
        mapped = {"btType": "BTFSValueMap-2062", "typeTag": "Box3d", "value": [
            {"btType": "BTFSValueMapEntry-2077", "key": {"btType": "BTFSValueString-1422", "value": "k"},
             "value": {"btType": "BTFSValueNumber-772", "value": 1}},
            {"btType": "BTFSValueMapEntry-2077", "key": {"btType": "BTFSValueNumber-772", "value": 2},
             "value": {"btType": "BTFSValueUndefined-2003"}}]}
        self.assertEqual(d(mapped), {"k": 1, "2": None, "_typeTag": "Box3d"})
        self.assertIsNone(d({"btType": "BTFSValueUndefined-2003"}))
        self.assertEqual(d({"btType": "BTFSValueTooBig-1247"}), {"too_big": True})
        self.assertEqual(d({"btType": "BTFSValueOther-1124", "type": "QUERY", "typeTag": "Query"}),
                         {"other": "QUERY", "typeTag": "Query"})
        unknown = {"btType": "BTFSValueSomethingNew-1", "value": 3}
        self.assertEqual(d(unknown), unknown)
        self.assertEqual(d(fake_onshape.fs_value({"error": "Fixture error text"})), {"error": "Fixture error text"})
        notice = {"btType": "BTNotice-227", "level": "ERROR", "type": "PARSE", "message": "m (fixture)",
                  "parameterId": "length", "locationInfos": [{"line": 3}, {"line": 4}]}
        self.assertEqual(fs.compact_notice(notice), {"level": "ERROR", "type": "PARSE", "message": "m (fixture)",
                                                     "parameter_id": "length", "location": {"line": 3}})

    def test_17_build_parameters(self):
        b = fs.build_parameters
        p = by_id(b(SPEC, {"length": "20 mm", "count": 3, "flag": True, "label": "x", "faces": "JHD"}))
        self.assertEqual(p["length"], {"btType": "BTMParameterQuantity-147", "parameterId": "length",
                                       "expression": "20 mm", "isInteger": False})
        self.assertEqual(p["count"]["expression"], "3")
        self.assertIs(p["count"]["isInteger"], True)
        self.assertEqual(p["flag"], {"btType": "BTMParameterBoolean-144", "parameterId": "flag", "value": True})
        self.assertEqual(p["label"], {"btType": "BTMParameterString-149", "parameterId": "label", "value": "x"})
        self.assertEqual(p["faces"]["queries"], [{"btType": "BTMIndividualQuery-138", "deterministicIds": ["JHD"]}])
        self.assertEqual(by_id(b(SPEC, {"faces": ["A", "B"]}))["faces"]["queries"][0]["deterministicIds"], ["A", "B"])
        with self.assertRaises(UsageError) as cm:
            b(SPEC, {"length": 20})
        self.assertIn('parameter length is a LENGTH quantity: give an expression with units, e.g. "20 mm"',
                      str(cm.exception))
        raw = {"btType": "BTMParameterAppearance-627", "color": {"red": 1}}
        self.assertEqual(by_id(b(SPEC, {"look": raw}))["look"], dict(raw, parameterId="look"))
        with self.assertRaises(UsageError):
            b(SPEC, {"look": "red"})
        with self.assertRaises(UsageError) as cm:
            b(SPEC, {"nope": 1})
        self.assertIn("valid parameter ids: length, count, flag, label, faces, corner, bound, look", str(cm.exception))
        defaults = by_id(b(SPEC, {}))
        self.assertEqual(defaults["length"]["expression"], "1 mm")
        self.assertIs(defaults["flag"]["value"], False)
        self.assertNotIn("faces", defaults)
        self.assertNotIn("bound", defaults)
        ns = fs.workspace_namespace(E, M)
        given = by_id(b(SPEC, {"corner": "ROUND", "bound": "BLIND"}, feature_namespace=ns,
                        studio_enums=frozenset({"PlateCorner"})))
        self.assertEqual(given["corner"], {"btType": "BTMParameterEnum-145", "parameterId": "corner", "value": "ROUND",
                                           "enumName": "PlateCorner", "namespace": ns})
        self.assertEqual(given["bound"]["namespace"], "")
        default = by_id(b(SPEC, {}, feature_namespace=ns, studio_enums=frozenset({"PlateCorner"})))
        self.assertEqual(default["corner"]["namespace"], ns)
        self.assertEqual(by_id(b(SPEC, {"corner": "ROUND"}, feature_namespace=ns))["corner"]["namespace"], "")
        with self.assertRaises(UsageError):
            b(SPEC, {"corner": "OVAL"})
        self.assertEqual(fs.find_spec([SPEC], "ACES plate"), SPEC)
        with self.assertRaises(UsageError) as cm:
            fs.find_spec([SPEC], "noSuchFeature")
        self.assertIn("acesPlate (ACES plate)", str(cm.exception))

    def test_18_namespaces_and_refs(self):
        ns = fs.workspace_namespace(E, M)
        self.assertEqual(ns, f"e{E}::m{M}")
        self.assertEqual(fs.parse_namespace(ns), {"document_id": None, "version_id": None, "element_id": E,
                                                  "microversion": M})
        vns = fs.version_namespace(D, V, E, M)
        self.assertEqual(vns, f"d{D}::v{V}::e{E}::m{M}")
        self.assertEqual(fs.parse_namespace(vns), {"document_id": D, "version_id": V, "element_id": E,
                                                   "microversion": M})
        for bad in ("", "e123::m456", f"e{E.upper()}::m{M}", f"x{E}::m{M}", None):
            self.assertIsNone(fs.parse_namespace(bad))
        r = ops.parse_ref(f"onshape://{D.upper()}/{V}/{E}")
        self.assertEqual(r, {"did": D, "wvm": "w", "wvmid": V, "eid": E, "canonical": f"onshape://{D}/{V}/{E}",
                             "host": None})
        self.assertEqual(ops.parse_ref(f"onshape://{D}/w/{V}/{E}")["canonical"], f"onshape://{D}/{V}/{E}")
        self.assertEqual(ops.parse_ref(f"onshape://{D}/v/{V}/{E}")["canonical"], f"onshape://{D}/v/{V}/{E}")
        with self.assertRaises(UsageError) as cm:
            ops.parse_ref(f"onshape://{D}/m/{M}/{E}")
        self.assertIn("STEP export needs a workspace or a version", str(cm.exception))
        self.assertEqual(ops.parse_ref(f"onshape://{D}/m/{M}/{E}", allow_microversion=True)["wvm"], "m")
        url = ops.parse_ref(f"https://Cad.Onshape.com/documents/{D}/w/{V}/e/{E}?configuration=x#frag")
        self.assertEqual((url["host"], url["canonical"]), ("cad.onshape.com", f"onshape://{D}/{V}/{E}"))
        ops.check_ref_host(url, "https://cad.onshape.com/api/v17")
        with self.assertRaises(UsageError) as cm:
            ops.check_ref_host(ops.parse_ref(f"https://evil.example/documents/{D}/w/{V}/e/{E}"),
                               "https://cad.onshape.com/api/v17")
        self.assertIn("refusing a document URL on host evil.example", str(cm.exception))
        for bad in ("onshape://xyz/abc/def", f"onshape://{D}/{V}", f"onshape://{D}/x/{V}/{E}", "ftp://x/y",
                    f"https://cad.onshape.com/documents/{D}/w/{V}", f"onshape://{D}/{V}/{E} ",
                    f"onshape://{D}/{V}/\t{E}"):
            with self.assertRaises(UsageError, msg=bad):
                ops.parse_ref(bad)
        with self.assertRaises(UsageError):
            fs.feature_messages_script('F"; x')

    def test_44_imports_and_enums(self):
        src = (FIXTURE_HEADER
               + 'export import(path : "onshape/std/geometry.fs", version : "2000.0");\n'
               + 'import(path : "AAAAAAAAAAAAAAAAAAAAAAAA", version : "bbbbbbbbbbbbbbbbbbbbbbbb");\n'
               + 'Lib::import(path : "cccccccccccccccccccccccc/dddddddddddddddddddddddd/eeeeeeeeeeeeeeeeeeeeeeee", '
                 'version : "ffffffffffffffffffffffff");\n'
               + '// import(path : "111111111111111111111111", version : "222222222222222222222222");\n'
               + '/* import(path : "333333333333333333333333", version : "444444444444444444444444"); */\n'
               + 'import(path : "data/table.csv", version : "1");\n'
               + 'export enum PlateCorner { annotation { "Name" : "Square" } SQUARE, ROUND }\n'
               + '// export enum Ignored { A }\n'
               + 'enum Private { B }\n')
        imports = fs.parse_imports(src)
        self.assertEqual([(i["kind"], i["path"]) for i in imports], [
            ("std", "onshape/std/common.fs"), ("std", "onshape/std/geometry.fs"),
            ("workspace", "AAAAAAAAAAAAAAAAAAAAAAAA"),
            ("linked", "cccccccccccccccccccccccc/dddddddddddddddddddddddd/eeeeeeeeeeeeeeeeeeeeeeee"),
            ("unrecognised", "data/table.csv")])
        self.assertTrue(imports[1]["exported"])
        self.assertFalse(imports[0]["exported"])
        self.assertEqual(imports[2]["element_id"], "a" * 24)
        self.assertEqual(imports[2]["version"], "b" * 24)
        self.assertEqual(imports[3]["alias"], "Lib")
        self.assertEqual((imports[3]["document_id"], imports[3]["version_id"], imports[3]["element_id"]),
                         ("c" * 24, "d" * 24, "e" * 24))
        self.assertIsNone(imports[0]["alias"])
        self.assertEqual(fs.declared_enums(src), {"PlateCorner"})

    def test_44b_import_scan_is_linear(self):
        # an alias is a whole identifier, never the tail of a longer word
        self.assertIsNone(fs.parse_imports('9Lib::import(path : "p", version : "v");')[0]["alias"])
        # the alias was tried from every position inside an identifier: quadratic in its length
        seconds = self.child_seconds(
            "import time\n"
            "from aces_onshape import featurescript as fs\n"
            "t0 = time.perf_counter()\n"
            "assert fs.parse_imports('a' * 200000) == []\n"
            "print(time.perf_counter() - t0)\n")
        self.assertLess(seconds, 1.0)

    def test_45_validators_reject_trailing_newline(self):
        checks = [
            lambda: C.validate_id(D + "\n", "did"),
            lambda: C.validate_wvm("w\n"),
            lambda: C.validate_feature_id("F1_0\n"),
            lambda: C.validate_xid("tid\n", "tid"),
            lambda: ops.validate_label("v1\n"),
            lambda: ops.validate_name("name\n"),
            lambda: ops.validate_feature_type("acesPlate\n"),
        ]
        for check in checks:
            with self.assertRaises(UsageError):
                check()
        with self.assertRaises(ProjectError):
            aces_project.validate_project_name("proj\n")
        self.assertIsNone(fs.parse_namespace(fs.workspace_namespace(E, M) + "\n"))
        self.assertEqual(C.validate_id(D.upper(), "did"), D)
        self.assertEqual(ops.validate_label("v1.2_x-y"), "v1.2_x-y")
        for bad in (".", "..", "a/b", "x" * 129):
            with self.assertRaises(UsageError):
                C.validate_xid(bad, "fid")


if __name__ == "__main__":
    unittest.main()
