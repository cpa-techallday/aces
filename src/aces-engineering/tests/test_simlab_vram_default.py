"""GPU-queue trap regression: a spec that omits resources.vram_gb must default to 0 (CPU lane).

gyroid-precooler SF-9 / conformal-antenna SF-2: the vendored simlab spec defaulted
``vram_gb`` to 8.0, and queue.py routes any ``vram_gb > 0`` to the GPU (`sim`) queue, so
CPU-only jobs that omitted the field sat in `waiting_for_gpu` behind vLLM leases. The
default is now 0.0; GPU solvers must declare their VRAM explicitly.
Run: python -m unittest discover -s tests -p "test_simlab_vram_default.py"."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
for _p in (TESTS.parent / "lib", TESTS.parent / "vendor"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from simlab import spec  # noqa: E402  (vendored simlab runtime)


class TestVramDefault(unittest.TestCase):
    def test_resources_default_is_cpu_lane(self):
        self.assertEqual(spec.Resources().vram_gb, 0.0)

    def test_experiment_without_resources_defaults_to_cpu_lane(self):
        exp = spec.Experiment(id="x", physics="cfd", solver="s")
        self.assertEqual(exp.resources.vram_gb, 0.0)
        # the queue routing predicate (vendor/simlab/queue.py: needs_gpu = vram_gb > 0)
        self.assertFalse(float(exp.resources.vram_gb) > 0)

    def test_declared_gpu_vram_still_routes_to_gpu(self):
        exp = spec.Experiment(id="x", physics="cfd", solver="s",
                              resources=spec.Resources(vram_gb=8.0))
        self.assertTrue(float(exp.resources.vram_gb) > 0)


if __name__ == "__main__":
    unittest.main()
