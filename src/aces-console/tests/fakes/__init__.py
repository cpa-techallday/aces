"""Stand-ins for the rig services, bound to 127.0.0.1 on ephemeral ports: fake_gc (GRID CONTROL rev B) and fake_rigstate
(the rig-state MCP). They replay the 2026-10-11 captures in tests/fixtures and record every request. Derived scenario
files live in scenarios/ and carry "derived": true with the capture they come from and the exact changes."""
