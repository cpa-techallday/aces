"""U01 of build spec section 21: the script contract read without running it (section 7.2, aces_cad.params)."""
from __future__ import annotations

import json
import math
import unittest

from base import CadTestCase


def src(params_src: str | None = None, entry: str = "def build(p):\n    return None\n") -> str:
    head = f"PARAMS = {params_src}\n\n\n" if params_src is not None else ""
    return head + entry


class ParamsTest(CadTestCase):

    def setUp(self):
        super().setUp()
        from aces_cad import errors, params
        self.errors, self.params = errors, params

    def parse(self, text: str):
        return self.params.parse_script(text)

    def refused(self, kind: str, fn, *args) -> dict:
        with self.assertRaises(self.errors.CadError) as ctx:
            fn(*args)
        err = ctx.exception.to_dict()
        self.assertEqual(err["kind"], kind, err)
        return err

    def test_u01_forms_short_long_and_none(self):
        info = self.parse(self.script("aces_plate"))
        self.assertEqual(info.entry, "build")
        self.assertTrue(info.entry_takes_p)
        self.assertEqual(sorted(info.declared), ["hole_d", "length", "thickness", "width"])
        self.assertEqual(info.declared["length"], {"type": "float", "default": 60.0, "min": 10.0, "max": 500.0,
                                                   "unit": "mm", "doc": "plate length along X"})
        short = self.parse(src('{"size": 10.0, "count": 3, "flag": True, "label": "a"}'))
        self.assertEqual({k: v["type"] for k, v in short.declared.items()},
                         {"size": "float", "count": "int", "flag": "bool", "label": "str"})
        self.assertEqual(short.declared["count"]["default"], 3)
        none = self.parse(self.script("two_hole_plate"))
        self.assertEqual(none.declared, {})
        self.assertEqual(self.params.resolve(none.declared, {}), {})
        ann = self.parse("PARAMS: dict = {\"a\": 1.5}\n\n\ndef build(p):\n    return None\n")
        self.assertEqual(ann.declared["a"]["type"], "float")

    def test_u01_not_literal_and_two_assignments(self):
        err = self.refused("params", self.parse, self.script("params_not_literal"))
        self.assertIn("PARAMS must be a literal dict", err["message"])
        self.refused("params", self.parse, src('{"a": 1.0}', "PARAMS = {\"b\": 2.0}\n\n\ndef build(p):\n"
                                                                "    return None\n"))
        self.refused("params", self.parse, src('{"a": 1.0 * 2}'))
        self.refused("params", self.parse, src("[1, 2]"))
        self.refused("params", self.parse, self.script("params_bad_key"))

    def test_u01_names(self):
        for bad in ('{"Length": 1.0}', '{"_x": 1.0}', '{"1x": 1.0}', '{"a-b": 1.0}', '{"' + "a" * 65 + '": 1.0}',
                    '{3: 1.0}'):
            self.refused("params", self.parse, src(bad))
        ok = self.parse(src('{"' + "a" * 64 + '": 1.0, "x_1": 2}'))
        self.assertEqual(len(ok.declared), 2)
        self.refused("params", self.parse, src("{" + ", ".join(f'"p{i}": {i}' for i in range(65)) + "}"))
        self.assertEqual(len(self.parse(src("{" + ", ".join(f'"p{i}": {i}' for i in range(64)) + "}")).declared),
                         64)

    def test_u01_inference_and_spec_keys(self):
        d = self.parse(src('{"a": {"default": 5}, "b": {"default": 5, "max": 10.0}, "c": {"default": True}, '
                              '"d": {"default": "x"}, "e": {"default": 2, "min": 1, "max": 3}, '
                              '"f": {"default": 1, "type": "float"}}')).declared
        self.assertEqual({k: v["type"] for k, v in d.items()},
                         {"a": "int", "b": "float", "c": "bool", "d": "str", "e": "int", "f": "float"})
        self.assertEqual(d["b"]["default"], 5)
        self.assertIsInstance(d["f"]["default"], float)
        for bad in ('{"a": {"default": 0.0, "min": 1.0}}', '{"a": {"default": 9.0, "max": 1.0}}',
                    '{"a": {"default": "q", "choices": ["x", "y"]}}', '{"a": {"min": 1.0}}',
                    '{"a": {"default": 1.0, "type": "complex"}}', '{"a": {"default": 1.0, "unit": "' + "m" * 17 + '"}}',
                    '{"a": {"default": 1.0, "doc": "' + "d" * 201 + '"}}', '{"a": {"default": "x", "choices": []}}',
                    '{"a": {"default": 1, "choices": [' + ", ".join(str(i) for i in range(33)) + ']}}',
                    '{"a": [1, 2]}', '{"a": None}'):
            self.refused("params", self.parse, src(bad))
        ok = self.parse(src('{"a": {"default": "x", "choices": ["x", "y"], "unit": "' + "m" * 16 + '", "doc": "'
                               + "d" * 200 + '"}}')).declared["a"]
        self.assertEqual(ok["choices"], ["x", "y"])

    def test_u01_overrides(self):
        declared = self.parse(src('{"length": {"default": 60.0, "min": 10.0, "max": 500.0}, "count": 3, '
                                     '"flag": False, "style": {"default": "a", "choices": ["a", "b"]}, "label": "x"}'
                                     )).declared
        resolved = self.params.resolve(declared, {"length": 70, "count": 4, "flag": True, "style": "b"})
        self.assertEqual(resolved, {"length": 70.0, "count": 4, "flag": True, "style": "b", "label": "x"})
        self.assertIsInstance(resolved["length"], float)
        self.assertEqual(self.params.resolve(declared, {}), {"length": 60.0, "count": 3, "flag": False, "style": "a",
                                                              "label": "x"})
        for bad in ({"count": True}, {"count": 4.5}, {"flag": 1}, {"length": "70"}, {"length": 5.0},
                    {"length": 501}, {"style": "c"}, {"label": "x" * 201}, {"label": "bad\x01"},
                    {"length": math.nan}, {"length": math.inf}, {"length": [70]}, {"length": None}):
            self.refused("params", self.params.resolve, declared, bad)
        err = self.refused("params", self.params.resolve, declared, {"lenght": 70.0})
        for name in ("length", "count", "flag", "style", "label"):
            self.assertIn(name, err["message"], "an unknown override names the declared parameters")
        self.refused("params", self.params.resolve, {}, {"anything": 1})

    def test_u01_entry_detection(self):
        self.assertEqual(self.parse(src(None, "def gen_step():\n    return None\n")).entry, "gen_step")
        self.assertFalse(self.parse(src(None, "def gen_step():\n    return None\n")).entry_takes_p)
        self.assertTrue(self.parse(src(None, "def gen_step(p):\n    return None\n")).entry_takes_p)
        self.assertEqual(self.parse(self.script("gen_step_plate")).entry, "gen_step")
        for bad in ("def make(p):\n    return None\n", "def build():\n    return None\n",
                    "def build(p, q):\n    return None\n", "def gen_step(a, b):\n    return None\n",
                    "x = 1\n"):
            err = self.refused("script", self.parse, src(None, bad))
            self.assertNotIn("Traceback", err["message"])
        err = self.refused("script", self.parse, src(None, "x = 1\n"))
        self.assertIn("def build(p)", err["message"])

    def test_u01_syntax_error_line_and_offset(self):
        err = self.refused("script", self.parse, self.script("syntax_error"))
        self.assertEqual(err["line"], self.expected_line())
        self.assertIsInstance(err.get("offset"), int)
        self.assertGreater(err["offset"], 0)
        self.assertEqual(err["exit_code"], 1)

    def expected_line(self) -> int:
        from base import expected
        return expected("errors.syntax_error")["line"]

    def test_u01_script_text_rules(self):
        self.refused("usage", self.parse, "")
        self.refused("usage", self.parse, "x = 1\0\n")
        self.refused("usage", self.parse, "x = '\ud800'\n")
        self.refused("usage", self.parse, "#" * 200_001)
        self.assertEqual(self.parse("#" * (200_000 - 30) + "\ndef build(p):\n    return 1\n").entry, "build")

    def test_u01_canonical_json(self):
        cj = self.params.canonical_json
        self.assertEqual(cj({"b": 1, "a": "é", "c": [1.0, True, None]}),
                         '{"a":"\\u00e9","b":1,"c":[1.0,true,null]}')
        self.assertEqual(cj({"x": 1, "y": 2}), cj({"y": 2, "x": 1}))
        with self.assertRaises(ValueError):
            cj({"x": math.nan})
        self.assertEqual(self.params.sha256_json({"a": 1}), self.params.sha256_json({"a": 1}))
        self.assertRegex(self.params.sha256_json({"a": 1}), r"^[0-9a-f]{64}$")
        self.assertEqual(json.loads(cj({"k": [1, 2]})), {"k": [1, 2]})


if __name__ == "__main__":
    unittest.main()
