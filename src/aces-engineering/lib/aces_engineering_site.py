"""Imported at interpreter start by aces-engineering.pth (venv site-packages). When ACES_SIMLAB_REGISTRY is set, the
vendored simlab reads its solver registry from that file instead of the read-only packaged one. aces-simlab sets it
for per-user registries, so `simlab run` worker processes resolve the same solvers as the command that started them.
Nothing happens when the variable is unset."""
import os

if os.environ.get("ACES_SIMLAB_REGISTRY"):
    try:
        import simlab.registry as _registry

        _registry.REGISTRY_PATH = os.environ["ACES_SIMLAB_REGISTRY"]
    except Exception:  # never break interpreter start-up
        pass
