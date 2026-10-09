# aces-survey: components and licences (read 2026-10-09)

| Component | Version shipped or pinned | Licence | Source |
|---|---|---|---|
| aces-survey scripts (`aces-webodm`, `aces-survey-setup`) | 0.1.0 | MIT | this repo |
| FlyPath QGIS plugin (vendored zip, installed per user by `aces-survey-setup`) | 3.0.0 (6 Oct 2026) | GPL-3.0; its `flypath_engine` is Apache-2.0 (NOTICE: Copyright 2026 Dronnix) | https://plugins.qgis.org/plugins/FlyPath/ and https://github.com/dronnix-io/FlyPath |
| WebODM (vendored tree: compose files, `webodm.sh`, web app source) | v3.3.0 (5 Sep 2026) | AGPL-3.0 | https://github.com/WebODM/WebODM |
| ODX engine and NodeODX (docker images `webodm/nodeodx`, `webodm/nodeodx:gpu`, pulled at run time, not shipped) | as published on Docker Hub | AGPL-3.0 | https://github.com/WebODM/ODX |
| WebODM web app and database images (`webodm/webodm_webapp`, `webodm/webodm_db`, pulled at run time, not shipped) | as published on Docker Hub | AGPL-3.0 | https://github.com/WebODM/WebODM |
| QGIS (dependency, Arch extra) | 4.2.2 | GPL-2.0-or-later | https://qgis.org |
| python-shapely, python-pyproj (dependencies, Arch extra) | 2.1.2, 3.8.0 | BSD-3-Clause, MIT | https://github.com/shapely/shapely and https://github.com/pyproj4/pyproj |
| CloudCompare (optional, AUR `cloudcompare`) | 2.13.2 | GPL-2.0-or-later | https://cloudcompare.org |
| COLMAP (optional, AUR `colmap`) | 4.2.0 | BSD-3-Clause | https://colmap.github.io |
| Meshroom (optional, AUR `meshroom`) | 2025.1.8 | MPL-2.0 | https://alicevision.org |

Distribution notes

- AGPL (WebODM, ODX) and GPL (FlyPath, QGIS, CloudCompare) components run as separate processes, containers or
  plugins; no `aces-*` code links against them.
- The WebODM tree shipped here is the unmodified v3.3.0 release archive
  (sha256 `c2d72ca2c8502ecb8b08561614a3fd1fefaf3a0d39a48c03ef063bbd58284ae6`), which is also the corresponding source
  for what the package ships. The docker images are pulled from Docker Hub by the operator at run time; their
  corresponding source is upstream's (WebODM/WebODM, WebODM/ODX, WebODM/NodeODX).
- The FlyPath zip shipped here is the unmodified plugin release
  (sha256 `2c9f6046fb2396b978e8494de53dc58f5c88f34635332da8a4cd567acfe05579`).
- WebODM decoupled from the OpenDroneMap organisation on 6 April 2026 (webodm.org/blog/announcement). OpenDroneMap's
  ODM (AGPL-3.0, v3.6.2) and NodeODM continue separately and can be attached to WebODM as another processing node.
- Trademark: "WebODM" names the upstream software only (github.com/WebODM/WebODM/blob/master/TRADEMARK.md); the
  package does not rebrand it.
