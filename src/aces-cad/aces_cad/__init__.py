"""aces-cad: local text-to-CAD for ACES.

build123d scripts run in a sandboxed child process of the aces-engineering venv (seccomp, Landlock, rlimits, a scrubbed
environment, no network); the results are inspected, rendered and drawn, and pulled into the ACES project as STEP +
parameters + sha256 with a cad.pull ledger entry. The server (aces-cad-mcp) and the CLI (aces-cad) run on the system
python3 with the standard library only; every piece of geometry is handled by a worker process (aces_cad.worker).
"""
from __future__ import annotations

__version__ = "0.1.0"
