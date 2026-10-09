# aces-os

ACES OS: the ACES computational-engineering platform packaged as an operating system on Omarchy 4 "Quattro", with an
ACES Server (the 8-GPU rig) as its compute. Planning repo, started 2026-10-08.

| File | What |
|---|---|
| `VISION.md` | ACES-OS-VISION-001: the vision and product plan (layering, compute fabric, pillars, engineering loop, field integration, creative suite, model policy, product, day one on DARKNESS, risks, 90-day plan, decisions) |
| `research/FACT_PACK.md` | Every fact the plan rests on, read from primary sources on 2026-10-08, with the brief, constraints, open questions and source list. Sections 14 and 15 are addenda (Omarchy packaging and shell IPC; field and device integration) |
| `docs/` | Design records; `docs/PACKAGES.md` is the package register (what each `aces-*` package is, its build and test state) |
| `pkgbuilds/<pkg>/PKGBUILD` | One Arch package per directory: the `aces-*` packages and pinned copies of the seven ArtCraft AUR `-bin` recipes |
| `src/<pkg>/` | Sources for the `aces-*` packages (packed into `pkgbuilds/<pkg>/<pkg>-src.tar.gz` by the build script) |
| `src/aces-cad-onshape/` | The Onshape adapter (package `aces-cad-onshape`): standard-library REST client, the `aces-onshape` CLI, the `aces-onshape-mcp` stdio MCP server and the text -> FeatureScript -> CAD -> STEP agent skill; `onshape://` pulls write STEP + parameters + sha256 into the project; writes, evaluation, exports and pulls are refused for sovereign and controlled projects |
| `bin/build [--nodeps] [pkg ...]` | Builds packages inside the `aces-builder` Docker image (Arch `base-devel`) and adds them to the local `[aces]` repo in `repo/x86_64/`; with no arguments it follows `pkgbuilds/ORDER` (members before dependants, `nodeps` flags for meta-packages); signs packages and the database when `keys/aces-pkgs.sec.asc` is present; logs in `logs/` |
| `bin/install-test [pkg ...]` | Installs the built packages into a clean `archlinux:base` container from the `[aces]` repo (trusting the signing key and requiring signatures when the database is signed), exercises the `aces` CLI and dry-runs dependency resolution for the meta-packages |
| `docker/builder.Dockerfile` | The builder image: `base-devel`, `namcap`, Python build tools, an unprivileged `builder` user, the `[aces]` repo pre-configured |
| `keys/` | `aces-pkgs.pub.asc` and `FINGERPRINT` are committed (ed25519 development signing key, expires 2028-10-08); the secret key and GnuPG home are ignored. Rotate before anything is distributed |
| `.github/workflows/build.yml` | CI: every push builds all packages in the builder container and runs the install test; the `ACES_SIGNING_KEY` secret makes the CI build signed |
| `docs/BENCHMARKS.md` | Lane qualification table appended by `aces lane bench` (every row is also a ledger entry) |
| `rehearsal/` | Omarchy rehearsal-VM kit for Hyper-V on DARKNESS (unattended install from a `cidata` drive, then the `[aces]` repo) |

Repository: private, `github.com/cpa-techallday/aces-os` (the sibling `cudax-sim-runtime` runtime repo is `github.com/cpa-techallday/cudax-sim-runtime`). Text files are LF on every platform (`.gitattributes`); bash scripts and PKGBUILDs break with CRLF.

Build everything from Git Bash on Windows (Docker Desktop) or from Linux:

```bash
bash bin/build && bash bin/install-test
```

The simulation runtime, generative design stack, SIMLAB console and evidence ledger live in `C:\Users\shedm\cudax-sim-runtime`
(see its `ACES_HANDOFF.md`). This repo is the OS and product layer above them.

Decisions already made (2026-10-08): DeepSeek-V4-Flash-0731 is the four-card model lane on GPUs 4–7 (GLM-5.2 stays the
seven-card flagship mode); DARKNESS dual-boots Windows and ACES OS; the seven ArtCraft apps ship on day one; the voxel kernel
is standard in every install; GPU 6 is in the working set.
