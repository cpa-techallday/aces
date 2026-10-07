"""formgen Studio: a failed run is reported, not lost in a NameError (audit L1).

Tk is replaced by a stand-in so this runs headless; the options and the error are test inputs.
"""
import importlib
import sys
import types

import pytest

pytest.importorskip("trimesh")


@pytest.fixture
def studio(monkeypatch):
    tk = types.ModuleType("tkinter")
    tk.Tk = type("Tk", (), {})
    for sub in ("filedialog", "messagebox", "ttk"):
        mod = types.ModuleType(f"tkinter.{sub}")
        setattr(tk, sub, mod)
        monkeypatch.setitem(sys.modules, f"tkinter.{sub}", mod)
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    monkeypatch.delitem(sys.modules, "formgen.studio", raising=False)
    mod = importlib.import_module("formgen.studio")
    yield mod
    sys.modules.pop("formgen.studio", None)


class FakeWindow:
    """What the worker thread touches on the Studio window."""

    def __init__(self):
        self.logged, self.callbacks, self.done = [], [], None

    def log(self, msg):
        self.logged.append(msg)

    def after(self, _ms, fn):
        self.callbacks.append(fn)  # Tk runs these later, on its own thread, after the worker's frame is gone

    def _done(self, res, exc):
        self.done = (res, exc)


def test_a_failed_run_reaches_the_window(studio, monkeypatch):
    def boom(opts, log):
        raise ValueError("no input file given (test input)")

    monkeypatch.setattr(studio, "run", boom)
    win = FakeWindow()
    studio.Studio._run_worker(win, opts=None)
    for fn in win.callbacks:
        fn()
    res, exc = win.done
    assert res is None and isinstance(exc, ValueError) and "test input" in str(exc)
    assert any("ValueError" in m for m in win.logged)


def test_a_good_run_reaches_the_window(studio, monkeypatch):
    monkeypatch.setattr(studio, "run", lambda opts, log: {"stats": {}})
    win = FakeWindow()
    studio.Studio._run_worker(win, opts=None)
    for fn in win.callbacks:
        fn()
    assert win.done == ({"stats": {}}, None)
