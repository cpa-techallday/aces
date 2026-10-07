#!/usr/bin/env bash
# Bit-rot fixes for the Palace v0.14.0 superbuild, discovered during the
# aces-em:0.1 rig build (2026-10, DARKNESS builder container). Run AFTER
# cloning palace, BEFORE cmake configure. Idempotent.
#
#   fix_superbuild.sh [PALACE_SRC_DIR]   (default /opt/palace-src)
#
# 1. sundials pin c3ff466... was deleted from the upstream LLNL/sundials
#    repo -> retarget tag v7.2.1.
# 2. GSLIB / LIBXSMM / libCEED / SLEPc are make-based externals, but the
#    superbuild invokes their build/install steps with
#    ${CMAKE_MAKE_PROGRAM} (= ninja under -G Ninja), which fails on their
#    plain Makefiles -> force real make.
set -euo pipefail

SRC="${1:-/opt/palace-src}"
cd "$SRC"

# 1. sundials tag retarget (value is on the line after set(EXTERN_SUNDIALS_GIT_TAG)
sed -i '/set(EXTERN_SUNDIALS_GIT_TAG/{n;s/"[^"]*"/"v7.2.1"/}' cmake/ExternalGitTags.cmake

# 2. make-based externals: ${CMAKE_MAKE_PROGRAM} -> make
sed -i 's/${CMAKE_MAKE_PROGRAM} ${GSLIB_OPTIONS} install/make ${GSLIB_OPTIONS} install/' \
    cmake/ExternalGSLIB.cmake
sed -i 's/BUILD_COMMAND     ${CMAKE_MAKE_PROGRAM} ${LIBXSMM_OPTIONS}/BUILD_COMMAND     make ${LIBXSMM_OPTIONS}/' \
    cmake/ExternalLIBXSMM.cmake
sed -i 's/${CMAKE_MAKE_PROGRAM} ${LIBCEED_OPTIONS} install/make ${LIBCEED_OPTIONS} install/' \
    cmake/ExternalLibCEED.cmake
sed -i 's/PETSC_ARCH= ${CMAKE_MAKE_PROGRAM} install/PETSC_ARCH= make install/; s/PETSC_ARCH= ${CMAKE_MAKE_PROGRAM}$/PETSC_ARCH= make/' \
    cmake/ExternalSLEPc.cmake
# MFEM builds with the Unix Makefiles generator but its smoke-test step ran
# ninja (no build.ninja present) -> force make there too.
sed -i 's/TEST_COMMAND      ${CMAKE_MAKE_PROGRAM} ex1 ex1p/TEST_COMMAND      make ex1 ex1p/' \
    cmake/ExternalMFEM.cmake

echo "fix_superbuild: patched $(grep -c 'make ${GSLIB_OPTIONS}' cmake/ExternalGSLIB.cmake) GSLIB," \
     "$(grep -c 'make ${LIBXSMM_OPTIONS}' cmake/ExternalLIBXSMM.cmake) LIBXSMM," \
     "$(grep -c 'make ${LIBCEED_OPTIONS}' cmake/ExternalLibCEED.cmake) libCEED," \
     "$(grep -c 'PETSC_ARCH= make' cmake/ExternalSLEPc.cmake) SLEPc," \
     "$(grep -c 'TEST_COMMAND      make ex1 ex1p' cmake/ExternalMFEM.cmake) MFEM-test," \
     "sundials=$(sed -n '/set(EXTERN_SUNDIALS_GIT_TAG/{n;p}' cmake/ExternalGitTags.cmake | tr -d ' "')"
