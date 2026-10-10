"""The aces-cad worker: one process per job on the aces-engineering venv's python.

main.py reads the job from stdin, installs the prelude (PDEATHSIG, nice, rlimits) and the kernel guards (seccomp,
then Landlock) before any heavy import, imports the op's libraries with stdout and stderr in log/import.txt, installs
the audit hook last in jobs that run agent code, runs the op and writes result.json atomically. Heavy libraries
(build123d, OCP, numpy, scipy, skimage, trimesh, matplotlib, ezdxf, lib3mf) are imported only inside functions.
"""
