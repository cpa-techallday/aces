"""Tier 0 proposers: suggest geometry; the verified tiers still sign off (D5)."""
from .contract import (ALLOWED_LICENCES, REJECTED, RESEARCH_ONLY, RESEARCH_STAMP,
                       LicenceError, Proposal, Proposer, available, get, register)
from .tpms_parametric import TpmsParametricProposer

register(TpmsParametricProposer())
