"""aces-console: the one ACES desktop console, backend half.

A per-user, loopback-only HTTP server written with the Python standard library (http.server, urllib, json,
threading). It imports the `aces` package (aces-base) in-process for the desktop's records and proxies the rig's
published HTTP and MCP ports (GRID CONTROL, rig-state, the SIMLAB console, MLflow) with every token held server-side.
Reads answer 200 with an envelope that carries staleness and the error text; the one consequential write, the rig
mode switch, is two-step, human-only and ledgered. Nothing here touches the rig host.
"""

__version__ = "0.1.0"
UI_NAME = f"aces-console {__version__}"
FIXTURE_STAMP = "FIXTURE 2026-10-11"
