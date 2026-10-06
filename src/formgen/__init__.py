"""formgen — turn Fusion 360 forms into triangulated, tessellated, printable lattices.

Pipeline: load (file *or* the live Fusion 360 session) -> remesh to target strut
length -> tessellate (skin | panel space-truss | infill) -> sequence for spatial
printing -> export (GLB/STL preview solids, lattice JSON, waypoint CSV, Yaskawa
INFORM .JBI) -> optional desktop-printable scale model STL.

Front-ends: `python -m formgen.cli` (terminal) and `python -m formgen.studio` (GUI).
"""

__version__ = "0.2.0"

from .loader import load_mesh
from .remesh import remesh_to_edge_length
from .lattice import build_lattice
from .graph import LatticeGraph
from .sequence import order_struts
from . import export, fusion, scale, solids, viewer  # noqa: F401
from .pipeline import Options, run  # noqa: F401
