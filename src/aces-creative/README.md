# aces-creative

Pulls in the seven ArtCraft Crafting Apps as pinned rebuilds of their AUR `-bin` recipes (kept in this repo under
`pkgbuilds/<app>-bin`), and ships `/usr/share/aces/creative/status.json` with upstream's own status words.
`aces-creative-labels` writes per-user launcher overrides so each app's launcher entry shows that status.

Apps: PhotoCraft, VectorCraft, FilmCraft, LightCraft, PdfCraft (still packaged as printcraft 0.2.1), EffectCraft,
DesignCraft. Licence upstream: MIT OR Apache-2.0. Agent control channels (CLI, JSON, MCP) are upstream features; the
ACES broker fronts them when the policy requires.
