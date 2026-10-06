"""Large-format lattice forms: formgen as an ACES manufacturing route.

``form.lattice`` takes a form (STEP/STL/OBJ/... or the live Fusion selection, via
formgen) and generates the triangulated lattice, print sequence, robot waypoints
and Yaskawa jobs for the HC10DTP cell, either as the product itself or as printed
formwork for foam fill and sprayed finishes.  It is a deterministic generator, not
a search model: ACES adds spec validation (sourced material data), route checks and
the evidence record around formgen's own pipeline.
"""
from .route import FormRequirements, run

__all__ = ["FormRequirements", "run"]
