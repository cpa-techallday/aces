# aces-engineering: components and licences

Read 2026-10-09 from the metadata of every distribution installed in the venv (what `pip show` reports: the
`License-Expression` field, else a one-line `License` field, else the trove classifiers), from the licence files
inside the wheels, and from upstream projects where a wheel states nothing. gmsh (added in 0.1.0-3) was read
2026-10-10 from the locked wheel itself (`gmsh-4.15.2-py2.py3-none-manylinux_2_24_x86_64.whl`, sha256
`4076a948...108c` as in `requirements.txt`): its METADATA, `LICENSE.txt` and `CREDITS.txt`, and the build options
its `libgmsh.so.4.15` reports. Nothing here has been reviewed by a lawyer. The operator policy (trial mode) allows
all of it on Carbon Project machines; the flags matter at the distribution gate (VISION.md section 12, decision 6).

## ACES code in this package

| Component | Licence |
|---|---|
| `aces_engineering` wrappers, shims, `sync.sh`, `lock.sh`, `lock.py` | MIT (Carbon Project Australia), as aces-base |
| vendored `simlab`, `gendesign` and the example cases from cudax-sim-runtime | **none yet**: no licence file in cudax-sim-runtime; VISION.md decision 4 says the kernel's source licence is decided before the first external install (`LicenseRef-ACES-undecided`). TODO: decide |

## Flags (not plainly permissive, or incomplete)

| Where | What | Why it matters |
|---|---|---|
| `cadquery-ocp-novtk` 7.9.3.1.1 | The wheel ships **no licence files**. Its PyPI metadata says Apache-2.0 (the OCP bindings), but `cadquery_ocp_novtk.libs/` bundles OpenCASCADE Technology 7.9.3 (`libTK*.so.7.9.3`, upstream licence LGPL-2.1-only with the Open CASCADE exception 1.0), FreeImage 3.18 (GPL-2.0/GPL-3.0 or the FreeImage Public License), LibRaw (LGPL-2.1 or CDDL-1.0), JBIG-KIT `libjbig` (GPL-2.0-or-later), OpenEXR 2.3 (BSD-3-Clause), libtiff, libjpeg, libpng, libwebp, OpenJPEG, Little CMS, FreeType, fontconfig, zstd, xz/liblzma, libuuid, jxrlib and libgomp | the licences in this row are the upstream projects' own (not stated in the wheel). TODO: confirm each against the cadquery-ocp build recipe before any distribution; LGPL/GPL parts need source offers and their licence texts shipped |
| `numpy` 2.4.6, `scipy` 1.17.1 | bundle OpenBLAS (BSD-3-Clause), libgfortran (GPL-3.0-or-later WITH GCC-exception-3.1) and libquadmath (LGPL-2.1-or-later), as stated in each wheel's LICENSE.txt | weak copyleft for libquadmath |
| `scikit-learn` 1.9.0 | bundles libgomp (GCC runtime library, GCC runtime exception), stated in its COPYING | runtime exception: normally no obligation |
| `lxml` 6.1.3 | BSD-3-Clause; its LICENSES.txt lists iconv as LGPL-2.1 | weak copyleft |
| `certifi`, `tqdm` | MPL-2.0 (tqdm: MPL-2.0 AND MIT) | weak copyleft at file level: modified files stay MPL |
| `embreex` 4.4.0 | its LICENSE is the two-clause BSD text; `embreex.libs/` bundles Intel Embree 4 and oneTBB with no licence files (both Apache-2.0 upstream) | add their licence texts before any distribution |
| `ocpsvg` 0.6.0 | no licence in its PyPI metadata; the upstream repository snoyer/ocpsvg carries an Apache-2.0 LICENSE | metadata gap only |
| `pillow` 12.3.0 | MIT-CMU; bundles freetype, harfbuzz, lcms2, libjpeg, libpng, libtiff, libwebp, libavif, openjpeg, brotli, zstd, xz, libxcb, libXau (texts in its LICENSE) | permissive; FreeType is used under the FTL |
| `gmsh` 4.15.2 | **GPL-2.0-or-later with the Gmsh exception** (below). The wheel is the Python API (`gmsh.py`, loading the library with ctypes) plus one native library, `libgmsh.so.4.15` (92,641,960 bytes), into which its build options compile OpenCASCADE and OpenCASCADE-CAF, Netgen, Mmg, MED, CGNS, METIS, PETSc (its BLAS and LAPACK), HXT, TetGen/BR, ALGLIB, ANN, Bamg, Blossom, Eigen, Gmm, Kbipack, MathEx, Voro++, TinyXML2, tinyobjloader, FLTK, OpenGL, libjpeg, libpng and zlib (`General.BuildOptions` of that library, read 2026-10-10). Only the Gmsh `LICENSE.txt` and `CREDITS.txt` ship in the wheel | strong copyleft: aces-engineering runs gmsh in-process in the mesher child (`python -m aces_engineering.golden.conforming mesh`), so a distributed venv carries GPL obligations (source of gmsh and of the bundled LGPL/GPL parts). The bundled components' licences are not stated in the wheel; upstream, several are copyleft (OpenCASCADE LGPL-2.1 with its exception, Netgen LGPL-2.1, Mmg and MED LGPL-3.0, FLTK LGPL-2.0 with its exceptions, ALGLIB's free edition GPL). Blossom comes from Concorde, whose own terms are for academic use. TODO: confirm each against the gmsh build recipe before any distribution |

Everything else below is MIT, BSD, Apache-2.0, ISC, PSF-2.0 or the PSF-based matplotlib licence. Not installed, so not
listed: cupy-cuda12x, warp-lang, torch (optional GPU); rq, redis, uvicorn, mcp, starlette, physicsnemo, rocketcea,
mlflow (rig-side or optional). The Python interpreter is Arch's `python` package (PSF-2.0), a dependency. CalculiX
`ccx`, which the conforming check runs as a separate process, is not in the venv: it is its own package,
`calculix-ccx-bin` (GPL-2.0-or-later; it statically contains SPOOLES, public domain, and ARPACK with LAPACK/BLAS
routines, BSD-3-Clause), an optional dependency here.

## LicenseRef-Gmsh-GPL-2.0-or-later-with-exceptions

The `license` array of the aces-engineering PKGBUILD names gmsh with this LicenseRef because SPDX has no identifier
for its terms. The wheel's METADATA says `License: GPLv2+` (classifier "GNU General Public License v2 or later
(GPLv2+)"), and its `share/doc/gmsh/LICENSE.txt` is the GNU GPL version 2 preceded by an exception from the Gmsh
copyright holders: Gmsh may be combined with code from the standard releases of Netgen, METIS, OpenCASCADE and
ParaView under those projects' own licences, provided their source code is included when the GPL requires source
distribution. People who modify Gmsh may drop the exception. `CREDITS.txt` lists the contributed codes a build may
contain, each under its own licence; which ones this build contains is in the flags table above.

## Distributions in the venv (78: the 77 locked in requirements.txt plus pip itself)

| Distribution | Version | Licence as stated | Field | Note |
|---|---|---|---|---|
| alembic | 1.20.0 | MIT | License-Expression |  |
| annotated-types | 0.8.0 | MIT | License-Expression |  |
| anytree | 2.13.0 | Apache-2.0 | License-Expression |  |
| asttokens | 3.0.2 | Apache 2.0 | License |  |
| build123d | 0.11.1 | Apache-2.0 | License |  |
| cadquery-ocp-novtk | 7.9.3.1.1 | Apache-2.0 | License | bundles OpenCASCADE (LGPL-2.1) and more; no licence files in the wheel (see below) |
| cadquery-ocp-proxy | 7.9.3.1.1 | Apache-2.0 | License |  |
| certifi | 2026.7.22 | MPL-2.0 | License | weak copyleft (MPL-2.0, file level) |
| charset-normalizer | 3.5.2 | MIT | License |  |
| colorlog | 6.12.0 | MIT License | License |  |
| contourpy | 1.4.0 | BSD-3-Clause | License-Expression |  |
| cycler | 0.12.1 | BSD License | classifier |  |
| embreex | 4.4.0 | (BSD two-clause text in the License field) | License | bundles Intel Embree 4 (`libembree4`) and oneTBB (`libtbb`), upstream licence Apache-2.0 for both, not stated in the wheel |
| executing | 2.2.1 | MIT | License |  |
| ezdxf | 1.4.4 | MIT License | classifier |  |
| fonttools | 4.66.1 | MIT | License |  |
| gmsh | 4.15.2 | GPLv2+ | License | GPL-2.0-or-later with the Gmsh exception (`LicenseRef-Gmsh-GPL-2.0-or-later-with-exceptions`, above); bundles `libgmsh` with OpenCASCADE, Netgen, Mmg, MED, PETSc and more (see the flag) |
| idna | 3.20 | BSD-3-Clause | License-Expression |  |
| ImageIO | 2.38.1 | BSD-2-Clause | License-Expression |  |
| ipython | 9.17.1 | BSD-3-Clause | License-Expression |  |
| ipython_pygments_lexers | 1.1.1 | BSD License | classifier |  |
| jedi | 0.20.0 | MIT | License |  |
| joblib | 1.5.3 | BSD-3-Clause | License-Expression |  |
| kiwisolver | 1.5.1 | BSD License | classifier |  |
| lazy-loader | 0.6 | BSD-3-Clause | License-Expression |  |
| lib3mf | 2.5.0 | BSD License | classifier |  |
| lxml | 6.1.3 | BSD-3-Clause | License | its LICENSES.txt lists iconv as LGPL-2.1 |
| Mako | 1.4.3 | MIT | License-Expression |  |
| Markdown | 3.11 | BSD-3-Clause | License-Expression |  |
| MarkupSafe | 3.0.4 | BSD-3-Clause | License-Expression |  |
| matplotlib | 3.11.1 | Python Software Foundation License | classifier |  |
| matplotlib-inline | 0.2.2 | BSD-3-Clause | License-Expression |  |
| mpmath | 1.3.0 | BSD | License |  |
| narwhals | 2.26.0 | MIT | License-Expression |  |
| networkx | 3.7 | BSD-3-Clause | License-Expression |  |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | License-Expression | bundles libgfortran (GCC runtime exception) and libquadmath (LGPL-2.1-or-later) |
| ocp_gordon | 0.2.2 | Apache-2.0 | License-Expression |  |
| ocpsvg | 0.6.0 | (none stated) | - | no licence in PyPI metadata; upstream repo snoyer/ocpsvg LICENSE is Apache-2.0 |
| optuna | 5.0.0 | MIT License | classifier |  |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | License-Expression |  |
| parso | 0.8.7 | MIT | License |  |
| pexpect | 4.9.0 | ISC license | License |  |
| pillow | 12.3.0 | MIT-CMU | License-Expression | bundles freetype, lcms2, libjpeg, libpng, libtiff, libwebp, openjpeg, zstd, xz and others (permissive; texts in its LICENSE) |
| pip | 26.2.1 | MIT | License-Expression |  |
| prompt_toolkit | 3.0.53 | BSD License | classifier |  |
| psutil | 7.2.2 | BSD-3-Clause | License |  |
| ptyprocess | 0.7.0 | ISC License (ISCL) | classifier |  |
| pure_eval | 0.2.4 | MIT | License |  |
| pydantic | 2.13.4 | MIT | License-Expression |  |
| pydantic_core | 2.46.4 | MIT | License-Expression |  |
| Pygments | 2.21.0 | BSD-2-Clause | License-Expression |  |
| pyparsing | 3.3.3 | MIT | License-Expression |  |
| python-dateutil | 2.9.0.post0 | Dual License | License | classifiers: BSD License, Apache Software License |
| python-docx | 1.2.0 | MIT | License |  |
| PyYAML | 6.0.3 | MIT | License |  |
| requests | 2.34.2 | Apache-2.0 | License |  |
| rtree | 1.4.1 | MIT | License-Expression |  |
| scikit-image | 0.26.0 | BSD License | classifier |  |
| scikit-learn | 1.9.0 | BSD-3-Clause | License-Expression | bundles libgomp (GCC runtime exception) |
| scipy | 1.17.1 | BSD License | classifier | bundles libgfortran (GCC runtime exception) and libquadmath (LGPL-2.1-or-later) |
| six | 1.17.0 | MIT | License |  |
| SQLAlchemy | 2.1.4 | MIT | License-Expression |  |
| stack-data | 0.6.3 | MIT | License |  |
| svgelements | 1.9.6 | MIT | License |  |
| svgpathtools | 1.8.0 | MIT | License |  |
| svgwrite | 1.4.3 | MIT License | License |  |
| sympy | 1.14.0 | BSD | License |  |
| threadpoolctl | 3.7.0 | BSD-3-Clause | License-Expression |  |
| tifffile | 2026.9.20 | BSD-3-Clause | License |  |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | License | weak copyleft (MPL-2.0 part) |
| traitlets | 5.16.1 | BSD License | classifier |  |
| trianglesolver | 1.2 | MIT | License |  |
| trimesh | 5.1.0 | MIT License | classifier |  |
| typing-inspection | 0.4.4 | MIT | License-Expression |  |
| typing_extensions | 4.16.0 | PSF-2.0 | License-Expression |  |
| urllib3 | 2.8.0 | MIT | License-Expression |  |
| wcwidth | 0.9.2 | MIT License | classifier |  |
| webcolors | 24.8.0 | BSD-3-Clause | License |  |
