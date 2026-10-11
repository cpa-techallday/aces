# aces-console: components and licences

Read 2026-10-11 from the files in this package: the licence headers of the vendored modules, the OFL texts beside the
fonts, and the cudax-sim-runtime checkout the Parameter Sky modules were copied from (commit `36d0446`, the HEAD of
`C:\Users\shedm\cudax-sim-runtime` on 2026-10-11, whose root holds no LICENSE file). Nothing here has been reviewed by a
lawyer. The PKGBUILD's `license` array names the three identifiers below. The operator policy (trial mode) allows all of
it on Carbon Project machines; the flags matter at the distribution gate (VISION.md section 12, decision 6).

| Component | Files | Licence |
|---|---|---|
| `aces_console` (the backend), the packaging, the shell's own front-end files (`index.html` as rewritten, `console.css`, `app.js`, `lib/*`, `plates/*`), the tests and fixtures manifests | `aces_console/**`, `static/index.html`, `static/console.css`, `static/app.js`, `static/lib/**`, `static/plates/**`, `tests/**`, `share/**`, `pyproject.toml` | MIT (Carbon Project Australia), as aces-base; `LICENSE` |
| Parameter Sky modules copied from cudax-sim-runtime `simlab/console/static` at commit `36d0446`: the token sheet with the two edits of the build spec (4.8) and the 390 px block, the fleet sky, the file loaders, the Three.js viewport, and the structure of `index.html` | `static/styles.css`, `static/sky.js`, `static/loaders.js`, `static/viewer.js` (one import line changed), `static/index.html` (structure) | **`LicenseRef-ACES-undecided`**: cudax-sim-runtime carries no licence file (listing of 2026-10-11); VISION.md decision 4 says the kernel's source licence is decided before the first external install, and aces-engineering labels its vendored cudax snapshot the same way (`src/aces-engineering/LICENSES.md`, `pkgbuilds/aces-engineering/PKGBUILD`). TODO: decide |
| Three.js r170 (`REVISION '170'`) and its OrbitControls addon, copied byte-identical from the same cudax checkout (`vendor/three.module.js` sha256 `ce1fa418de16a19495a9f72495580e3015d7745c296d3ce0485897f902ddedfb`, `vendor/OrbitControls.js` sha256 `80efaadea4f8a636a65fb0bd08bfef62f3d93a0bb94e2e7500f23176c5c07f4e`, 32,134 bytes) | `static/vendor/three.module.js`, `static/vendor/OrbitControls.js` | MIT (Copyright 2010-2024 Three.js Authors; `SPDX-License-Identifier: MIT` in the module header) |
| EB Garamond (italic 400, normal 500) and JetBrains Mono (Regular, Medium, Bold, Italic), self-hosted; the duplicate `EBGaramond-italic-500.woff2` of the cudax tree (byte-identical to the italic 400 file) is not shipped | `static/vendor/fonts/*.woff2` with `EBGaramond-OFL.txt` and `JetBrainsMono-OFL.txt` beside them | SIL Open Font License 1.1 (`OFL-1.1`): EB Garamond Copyright 2017 The EB Garamond Project Authors; JetBrains Mono Copyright 2020 The JetBrains Mono Project Authors |

Not vendored, read at run time only: the `aces` package (aces-base, MIT) imported in process; the rig's GRID CONTROL,
rig-state, SIMLAB console and MLflow services, reached over their published HTTP and MCP ports (nothing of theirs is
copied here). The Python interpreter is Arch's `python` package (PSF-2.0), a dependency. The test fixtures under
`tests/fixtures` and `tests/harness/fixtures` are live read-only captures of the rig's own replies (2026-10-10/11,
credential-looking lines stripped at capture time) and carry no licence of their own.
