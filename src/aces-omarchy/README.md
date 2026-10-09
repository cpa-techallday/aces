# aces-omarchy

The Omarchy coupling for ACES OS, as an overlay: nothing under `/usr/share/omarchy` is touched.

| Piece | Where it lives | What it does |
|---|---|---|
| `post-boot.d/aces-fabric` | `~/.config/omarchy/hooks/post-boot.d/` (symlink) | refreshes the lane manifest and labels the creative launchers, in the background |
| `post-update.d/aces-fabric` | `~/.config/omarchy/hooks/post-update.d/` (symlink) | re-runs the setup and refreshes the manifest after an Omarchy update |
| `aces-engineering` skill | `~/.claude/skills`, `~/.codex/skills`, `~/.pi/agent/skills`, `~/.agents/skills` (symlinks, where present) | teaches agents the ACES rules and commands |
| `aces-fabric` plugin | `~/.config/omarchy/plugins/aces-fabric` (symlink) | bar widget showing the server mode, GPU load and lanes from `fabric.json` |
| `aces-omarchy-setup` | `/usr/bin` | links all of the above for the current user; `--remove` undoes it |

Third-party plugins land disabled in Omarchy; enable `aces-fabric` from the plugin menu after setup. The plugin was
written against the published manifest contract and could not be validated on this build host (no omarchy-shell
here); the setup script runs `omarchy plugin validate` when it is available and reports the result.
