"""Shared test inputs.

The requirement values below exist only to exercise the code. They are not a
customer requirement, a material datasheet or a measured result, and must never be
quoted as such.
"""
import copy

import pytest

TEST_SPEC = {
    "aces": 1,
    "domain": "hx.two_fluid",
    "project": "unit-test",
    "owner": "test",
    "company": "CPA",
    "rd_activity": "ACES unit tests (test inputs only)",
    "hypothesis": "The engine behaves consistently on test inputs.",
    "requirements": {
        "duty_W": 10_000.0,
        "hot": {"fluid": "Water", "mass_flow_kg_s": 0.2, "inlet_temperature_K": 353.15,
                "inlet_pressure_Pa": 2e5, "max_pressure_drop_Pa": 2e4},
        "cold": {"fluid": "Water", "mass_flow_kg_s": 0.2, "inlet_temperature_K": 293.15,
                 "inlet_pressure_Pa": 2e5, "max_pressure_drop_Pa": 2e4},
        "material": {"name": "test-material", "thermal_conductivity_W_mK": 15.0,
                     "density_kg_m3": 8000.0, "source": "test input only"},
        "manufacturing": {"process": "lpbf", "min_wall_m": 3e-4, "min_channel_m": 1e-3,
                          "envelope_length_m": 0.15, "envelope_width_m": 0.1, "envelope_height_m": 0.1},
    },
    "search": {"trials": 60, "seed": 3, "population": 20},
}


@pytest.fixture
def spec_dict():
    return copy.deepcopy(TEST_SPEC)
