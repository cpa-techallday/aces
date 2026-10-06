"""Domain design models.  Register a model here to make it reachable from the CLI."""
from .hx import TwoFluidTPMS
from .radar import PhasedArrayRadar

MODELS = {TwoFluidTPMS.id: TwoFluidTPMS, PhasedArrayRadar.id: PhasedArrayRadar}


def get(domain: str):
    try:
        return MODELS[domain]
    except KeyError:
        raise KeyError(f"unknown domain {domain!r}; registered: {sorted(MODELS)}") from None
