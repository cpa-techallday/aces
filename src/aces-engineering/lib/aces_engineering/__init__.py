"""aces-engineering: desktop wrappers over the vendored simlab and gendesign snapshot of cudax-sim-runtime.

Installed layout: the venv at /opt/aces/engineering/venv, the vendored code and these wrappers in
/opt/aces/engineering/lib (put on the venv's path by aces-engineering.pth), shims in /usr/bin.
"""
from __future__ import annotations

import os
import shutil
import sys
import tomllib
from pathlib import Path

__version__ = "0.1.0"

LIB = Path(__file__).resolve().parent.parent
SHARE = Path(os.environ.get("ACES_ENGINEERING_SHARE", "/usr/share/aces/engineering"))


def data_dir() -> Path:
    """Per-user simlab data: the evidence ledger and the optional per-user solver registry."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "aces" / "simlab"


def aces_config() -> dict:
    """The aces-base configuration files, same order as `aces`: /etc/aces/aces.toml, then ~/.config/aces/aces.toml."""
    etc = Path(os.environ.get("ACES_ETC", "/etc/aces"))
    user = Path(os.environ.get("ACES_USER_CONFIG", Path.home() / ".config" / "aces"))
    cfg: dict = {}
    for path in (etc / "aces.toml", user / "aces.toml"):
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except FileNotFoundError:
            continue
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise SystemExit(f"aces-engineering: cannot read {path}: {exc}") from exc
        for key, val in data.items():
            if isinstance(val, dict) and isinstance(cfg.get(key), dict):
                cfg[key] = {**cfg[key], **val}
            else:
                cfg[key] = val
    return cfg


def server_url(key: str) -> str | None:
    return (aces_config().get("server") or {}).get(key)


def apply_registry() -> None:
    """Point simlab at the registry named by ACES_SIMLAB_REGISTRY (the packaged one is read-only)."""
    path = os.environ.get("ACES_SIMLAB_REGISTRY")
    if path:
        import simlab.registry

        simlab.registry.REGISTRY_PATH = path


def ensure_user_registry() -> Path:
    """Copy the packaged registry to the per-user one before a command that registers a solver."""
    if os.environ.get("ACES_SIMLAB_REGISTRY"):
        return Path(os.environ["ACES_SIMLAB_REGISTRY"])
    import simlab.registry

    dst = data_dir() / "solvers.json"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(simlab.registry.REGISTRY_PATH, dst)
        print(f"aces-engineering: copied the packaged solver registry to {dst}; registrations go there", file=sys.stderr)
    os.environ["ACES_SIMLAB_REGISTRY"] = str(dst)
    apply_registry()
    return dst


def prepare_env() -> None:
    """Desktop defaults for the vendored simlab, only where the caller has not set them: evidence goes to the per-user
    data dir (the module default is <install>/out/evidence, which is read-only), and a per-user registry is used when
    one exists. Child processes (simlab run workers) inherit both through the environment."""
    os.environ.setdefault("SIMLAB_EVIDENCE_ROOT", str(data_dir() / "evidence"))
    user_reg = data_dir() / "solvers.json"
    if "ACES_SIMLAB_REGISTRY" not in os.environ and user_reg.is_file():
        os.environ["ACES_SIMLAB_REGISTRY"] = str(user_reg)
    apply_registry()


def optional_status() -> dict:
    """Which optional (GPU or rig-side) packages this venv can import. Nothing here installs anything."""
    out = {}
    for mod in ("cupy", "warp", "torch", "physicsnemo", "rocketcea", "gmsh", "mlflow", "rq", "redis", "mcp", "uvicorn"):
        try:
            __import__(mod)
            out[mod] = "importable"
        except Exception as exc:  # ImportError, or a GPU library failing to initialise
            out[mod] = f"not available ({type(exc).__name__})"
    return out
