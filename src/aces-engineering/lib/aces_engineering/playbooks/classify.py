"""Deterministic brief classifier: brief text -> part class.

A rule table over a vocabulary of keyword/regex families, unit hints and material hints
(design doc section 2, layer 1). Pure python, no LLM calls: every match is recorded as
evidence so the plan can show *why* a class was chosen. Unknown briefs fall back to
`structure` with `low_confidence` set (the console asks the user to confirm before launch).

Scoring: each rule carries a weight (3 distinctive, 2 strong, 1 weak). A class's score is
the sum of its matched rule weights. Confidence is the winner's share of the top two scores
(1.0 when nothing else matched, 0.5 on an exact tie). `low_confidence` is set when nothing
matched or two classes tie at the top.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import CLASSES, DEFAULT_CLASS

# ---- the rule table ------------------------------------------------------------------------------------------------
# (class, pattern, weight, label). Patterns are case-insensitive regexes; use \b for word
# stems. The label is what the evidence list quotes.
RULES: tuple[tuple[str, str, int, str], ...] = (
    # structure: chassis / bracket / frame and general load-bearing vocabulary
    ("structure", r"\bchassis\b", 3, "chassis"),
    ("structure", r"\bbrackets?\b", 3, "bracket"),
    ("structure", r"\bframes?\b", 2, "frame"),
    ("structure", r"\bmounts?\b", 2, "mount"),
    ("structure", r"\bsuspension\b", 2, "suspension"),
    ("structure", r"\bload[- ]bearing\b", 2, "load-bearing"),
    ("structure", r"\bstiff(ness)?\b", 1, "stiffness"),
    ("structure", r"\bbeams?\b", 1, "beam"),
    ("structure", r"\boff[- ]road(er)?\b", 1, "off-roader"),
    ("structure", r"\bwheeled\b|\bwheels?\b", 1, "wheeled"),
    ("structure", r"\bstructur(e|al)\b", 1, "structure"),
    ("structure", r"\bmonocoque\b", 2, "monocoque"),
    ("structure", r"\bsubframe\b", 2, "subframe"),
    ("structure", r"\bcradle\b", 1, "cradle"),
    # flow-device: duct / intake / nozzle / aero surfaces
    ("flow-device", r"\bducts?\b", 3, "duct"),
    ("flow-device", r"\bintakes?\b", 3, "intake"),
    ("flow-device", r"\bmanifolds?\b", 3, "manifold"),
    ("flow-device", r"\bnozzles?\b", 2, "nozzle"),
    ("flow-device", r"\bdiffusers?\b", 2, "diffuser"),
    ("flow-device", r"\bwings?\b", 2, "wing"),
    ("flow-device", r"\bairflow\b", 2, "airflow"),
    ("flow-device", r"\binlets?\b", 2, "inlet"),
    ("flow-device", r"\bpressure (drop|loss)\b", 2, "pressure drop"),
    ("flow-device", r"\baero(dynamic(s)?|foil)?\b", 1, "aero"),
    ("flow-device", r"\bpipes?\b", 1, "pipe"),
    ("flow-device", r"\bexhaust\b", 1, "exhaust"),
    ("flow-device", r"\bdrag\b|\blift\b", 1, "drag/lift"),
    # heat-rejection: heatsink / cooler / heat exchanger / cold plate
    ("heat-rejection", r"\bheat[ -]?exchangers?\b", 3, "heat exchanger"),
    ("heat-rejection", r"\bheat[ -]?sinks?\b", 3, "heatsink"),
    ("heat-rejection", r"\bcold[ -]?plates?\b", 3, "cold plate"),
    ("heat-rejection", r"\bradiators?\b", 2, "radiator"),
    ("heat-rejection", r"\bcool(ing|er|ers)\b", 2, "cooling"),
    ("heat-rejection", r"\bthermal\b", 1, "thermal"),
    ("heat-rejection", r"\bheat rejection\b", 2, "heat rejection"),
    ("heat-rejection", r"\bcht\b|\bconjugate heat\b", 2, "CHT"),
    ("heat-rejection", r"\bdissipat(e|ion|or)\b", 1, "dissipation"),
    # em-device: antenna / radome / waveguide / RF
    ("em-device", r"\bantennas?\b", 3, "antenna"),
    ("em-device", r"\bradomes?\b", 3, "radome"),
    ("em-device", r"\bwaveguides?\b", 3, "waveguide"),
    ("em-device", r"\bphased[ -]array\b", 3, "phased array"),
    ("em-device", r"\bs[ -]?param(eter)?s?\b", 2, "S-parameter"),
    ("em-device", r"\belectromagnetic(s)?\b", 2, "electromagnetic"),
    ("em-device", r"\bradars?\b", 2, "radar"),
    ("em-device", r"\brf\b", 2, "RF"),
    ("em-device", r"\bgain\b|\bbandwidth\b", 1, "gain/bandwidth"),
    # rotating-machine: pump / compressor / turbine / fan
    ("rotating-machine", r"\bturbines?\b", 3, "turbine"),
    ("rotating-machine", r"\bcompressors?\b", 3, "compressor"),
    ("rotating-machine", r"\bimpellers?\b", 3, "impeller"),
    ("rotating-machine", r"\bpumps?\b", 2, "pump"),
    ("rotating-machine", r"\bfans?\b", 2, "fan"),
    ("rotating-machine", r"\brotors?\b", 2, "rotor"),
    ("rotating-machine", r"\bpropellers?\b", 2, "propeller"),
    ("rotating-machine", r"\bturbomachinery\b", 3, "turbomachinery"),
    ("rotating-machine", r"\belectric motor\b|\bmotor(s)?\b", 2, "motor"),
    ("rotating-machine", r"\bblades?\b", 1, "blade"),
    # rocket-motor: rocket / thruster / propellant / combustion chamber
    ("rocket-motor", r"\brocket motor\b", 3, "rocket motor"),
    ("rocket-motor", r"\brockets?\b", 3, "rocket"),
    ("rocket-motor", r"\bthrusters?\b", 3, "thruster"),
    ("rocket-motor", r"\bpropellants?\b", 3, "propellant"),
    ("rocket-motor", r"\bcombustion chamber\b", 3, "combustion chamber"),
    ("rocket-motor", r"\bspecific impulse\b|\bisp\b", 3, "specific impulse"),
    ("rocket-motor", r"\bchamber pressure\b", 2, "chamber pressure"),
    ("rocket-motor", r"\bthrust\b", 2, "thrust"),
    ("rocket-motor", r"\bnozzle\b", 1, "nozzle"),
)

# Unit and material hints: weak class votes from the physics vocabulary of the brief.
UNIT_HINTS: tuple[tuple[str, str, int, str], ...] = (
    ("structure", r"\bmpa\b|\bgpa\b", 1, "stress unit (MPa/GPa)"),
    ("structure", r"\bkn\b", 1, "load unit (kN)"),
    ("em-device", r"\bghz\b|\bmhz\b|\bdb\b|\bdbi\b", 2, "RF unit (GHz/MHz/dB)"),
    ("rotating-machine", r"\brpm\b", 2, "rotational unit (rpm)"),
    ("heat-rejection", r"\b\d+(?:\.\d+)?\s*w\b|\bdegc\b|°c\b|\bkelvin\b", 1, "thermal unit (W/°C)"),
    ("rocket-motor", r"\bs\b\s*isp|isp\s*of", 1, "Isp mention"),
)

MATERIAL_HINTS: tuple[tuple[str, str, int, str], ...] = (
    ("structure", r"\bsteel\b|\balumini?um\b|\btitanium\b|\bcarbon[ -]fib(er|re)\b", 1, "structural material"),
    ("heat-rejection", r"\bcopper\b", 1, "copper (heat spreading)"),
    ("rocket-motor", r"\binconel\b|\bregenerative(ly)?\b", 1, "rocket material/regen"),
)

_COMPILED = [(cls, re.compile(pat, re.IGNORECASE), w, label) for cls, pat, w, label in RULES + UNIT_HINTS + MATERIAL_HINTS]

# A match below this winner score is treated as a guess even when it is the top score.
MIN_CONFIDENT_SCORE = 3


@dataclass
class Classification:
    """The classifier's answer: the class, a confidence in [0, 1], and the matched evidence."""
    cls: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    low_confidence: bool = False
    scores: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"class": self.cls, "confidence": self.confidence, "low_confidence": self.low_confidence,
                "evidence": list(self.evidence), "scores": dict(self.scores)}


def classify(brief: str) -> Classification:
    """Classify a brief into a part class. Deterministic; never raises on text input."""
    text = (brief or "").lower()
    scores: dict[str, int] = {c: 0 for c in CLASSES}
    evidence: list[str] = []
    for cls, rx, weight, label in _COMPILED:
        if rx.search(text):
            scores[cls] += weight
            evidence.append(f'"{label}" -> {cls} (+{weight})')
    ranked = sorted(CLASSES, key=lambda c: (-scores[c], c))
    top, second = ranked[0], ranked[1]
    top_score, second_score = scores[top], scores[second]
    low = top_score < MIN_CONFIDENT_SCORE or second_score == top_score
    if low and top_score == 0:
        evidence.append("no class vocabulary matched; defaulted to structure")
    elif low and second_score == top_score and top_score > 0:
        evidence.append(f"tie between {top} and {second}; defaulted to {DEFAULT_CLASS}")
    confidence = round(top_score / (top_score + second_score), 2) if top_score + second_score else 0.0
    winner = top
    if top_score < MIN_CONFIDENT_SCORE or second_score == top_score:
        winner = DEFAULT_CLASS  # a weak or tied top match is a guess: take the safe default class
    return Classification(cls=winner, confidence=confidence, evidence=evidence,
                          low_confidence=low, scores=scores)
