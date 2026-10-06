"""Two-fluid TPMS heat-exchanger core: the first ACES design model.

Encoded logic, in order:
  1. Geometry: a sheet TPMS (gyroid, diamond or primitive) of cell size a and wall
     ratio t/a, measured numerically (``tpms.unit_cell``).  Hot fluid in network A,
     cold fluid in network B, counterflow along the core length.
  2. Manufacturability: the measured thinnest wall and the channel neck (largest
     sphere that can pass through each network) against the printer limits;
     isolated fluid pockets (trapped powder) rejected; at least ``min_cells_across``
     cells in every direction so the homogenised treatment holds.
  3. Hydraulics: interstitial velocity from the fluid's volume fraction of the
     frontal area; Reynolds number on the measured hydraulic diameter.
  4. Heat transfer: straight-duct Nusselt and friction baselines x calibration
     factors; overall UA through both films and the wall; counterflow e-NTU.
     Fluid properties re-evaluated at mean stream temperatures until converged.
  5. Pressure drop: Darcy over the flow path (core length x tortuosity factor).

What this tier does NOT model (reported on every result): inlet/outlet headers
and manifolds, flow maldistribution, entrance and developing-flow effects, wall
roughness, compressibility at large pressure drop, axial wall conduction.  The
calibration factors start at 1.0; until simlab CFD or rig tests replace them the
results are ranking estimates, not performance claims.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, field_validator, model_validator

from ... import correlations as corr
from ... import fluids, manufacturing
from ...calibration import Registry
from ...model import Evaluation, Param
from . import tpms

ALWAYS_WARN = (
    "core only: headers, manifolds and flow maldistribution not modelled",
    "fast tier: straight-duct correlations applied to a TPMS core; verify on simlab before use",
)
ISOLATED_LIMIT = 1e-3  # share of a fluid volume allowed to sit in pockets cut off from the network


class Stream(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fluid: str = Field(description="CoolProp fluid name, e.g. Water, Air, INCOMP::MEG[0.5]")
    mass_flow_kg_s: PositiveFloat
    inlet_temperature_K: PositiveFloat
    inlet_pressure_Pa: PositiveFloat
    max_pressure_drop_Pa: PositiveFloat


class Material(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    thermal_conductivity_W_mK: PositiveFloat
    density_kg_m3: PositiveFloat
    source: str = Field(min_length=3, description="datasheet or standard the property values come from")


class Manufacturing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    process: str = Field(description="manufacturing route id from aces.manufacturing (lpbf, polymer_extrusion)")

    @field_validator("process")
    @classmethod
    def _known_route(cls, v: str) -> str:
        manufacturing.require(v, "hx.two_fluid")
        return v

    min_wall_m: PositiveFloat
    min_channel_m: PositiveFloat = Field(description="smallest open passage (neck) for powder removal and fouling")
    envelope_length_m: PositiveFloat = Field(description="flow direction")
    envelope_width_m: PositiveFloat
    envelope_height_m: PositiveFloat


class HXRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hot: Stream
    cold: Stream
    duty_W: PositiveFloat
    material: Material
    manufacturing: Manufacturing
    tpms_types: list[Literal["gyroid", "diamond", "primitive"]] = Field(default_factory=lambda: list(tpms.KINDS))
    calibration_file: str | None = Field(None, min_length=1, description="registry JSON, relative to the spec")
    # Laminar baseline: uniform wall temperature (Nu 3.66) is the conservative default;
    # uniform heat flux (Nu 4.36) is closer for balanced counterflow but 19 % higher.
    thermal_boundary: Literal["uniform_wall_temperature", "uniform_heat_flux"] = "uniform_wall_temperature"
    min_cells_across: int = Field(3, ge=2, description="fewest cells in any direction for the homogenised model")

    @model_validator(mode="after")
    def _hot_is_hotter(self):
        if self.hot.inlet_temperature_K <= self.cold.inlet_temperature_K:
            raise ValueError("hot.inlet_temperature_K must exceed cold.inlet_temperature_K")
        if not self.tpms_types:
            raise ValueError("tpms_types must list at least one of gyroid, diamond, primitive")
        return self


class TwoFluidTPMS:
    id = "hx.two_fluid"
    version = "0.2.0"
    requirements_model = HXRequirements

    def __init__(self, resolution: int = 64, registry: Registry | None = None):
        self.resolution = resolution
        self._fixed_registry = registry
        self._registries: dict[str | None, Registry] = {}

    # ------------------------------------------------------------------ parameters
    def parameters(self, req: HXRequirements) -> list[Param]:
        m = req.manufacturing
        smallest = min(m.envelope_length_m, m.envelope_width_m, m.envelope_height_m)
        # No cell smaller than min_channel / (largest measured neck/a) can pass the neck check.
        neck_max = tpms.max_neck_over_a(tuple(req.tpms_types)) or 0.4
        cell_lo = m.min_channel_m / neck_max
        cell_hi = max(cell_lo * 1.5, smallest / req.min_cells_across)
        return [
            Param("tpms", "categorical", choices=tuple(req.tpms_types)),
            Param("cell_m", "float", cell_lo, cell_hi, log=True, units="m"),
            Param("wall_ratio", "float", 0.03, 0.35, units="t/a"),
            Param("length_m", "float", 0.1 * m.envelope_length_m, m.envelope_length_m, units="m"),
            Param("width_m", "float", 0.1 * m.envelope_width_m, m.envelope_width_m, units="m"),
            Param("height_m", "float", 0.1 * m.envelope_height_m, m.envelope_height_m, units="m"),
        ]

    def registry(self, req: HXRequirements) -> Registry:
        if self._fixed_registry is not None:
            return self._fixed_registry
        key = req.calibration_file
        if key not in self._registries:
            self._registries[key] = Registry.load(key)
        return self._registries[key]

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, params: dict[str, Any], req: HXRequirements) -> Evaluation:
        kind = params["tpms"]
        a = float(params["cell_m"])
        ratio = float(params["wall_ratio"])
        L, W, H = float(params["length_m"]), float(params["width_m"]), float(params["height_m"])
        reg = self.registry(req)
        f_nu = reg.get(f"hx.{kind}.nusselt")
        f_f = reg.get(f"hx.{kind}.friction")
        f_tau = reg.get(f"hx.{kind}.tortuosity")
        uncal = [f.key for f in (f_nu, f_f, f_tau) if not f.calibrated]
        warnings = list(ALWAYS_WARN)

        uc = tpms.unit_cell(kind, ratio, self.resolution)
        mf = tpms.manufacturability(kind, ratio)
        mid = tpms.mid_surface_area(kind)
        t_mean = uc.solid_fraction / mid * a  # mean wall actually present (conduction)
        t_min = mf.wall_min_over_a * a  # thinnest wall (manufacturing)
        V = L * W * H
        Af = W * H
        sides = {}
        for i, name in ((0, "hot"), (1, "cold")):
            phi = uc.fluid_fraction[i]
            av = uc.area_density_a[i] / a
            sides[name] = {"phi": phi, "area_m2": av * V, "dh_m": (4.0 * phi / av) if av > 0 else 0.0,
                           "neck_m": mf.neck_over_a[i] * a, "isolated": mf.isolated_fraction[i]}

        hot, cold = req.hot, req.cold
        Th, Tc = hot.inlet_temperature_K, cold.inlet_temperature_K
        Th_m, Tc_m = Th, Tc
        out: dict[str, Any] = {}
        for _ in range(8):
            res = {}
            for name, stream, Tm in (("hot", hot, Th_m), ("cold", cold, Tc_m)):
                s = sides[name]
                st = fluids.state(stream.fluid, Tm, stream.inlet_pressure_Pa)
                u = stream.mass_flow_kg_s / (st.rho * s["phi"] * Af)
                re = st.rho * u * s["dh_m"] / st.mu
                nu = corr.nusselt(re, st.pr, req.thermal_boundary)
                ff = corr.friction_factor_darcy(re)
                h = nu.value * f_nu.value * st.k / s["dh_m"]
                dp = ff.value * f_f.value * (f_tau.value * L / s["dh_m"]) * 0.5 * st.rho * u * u
                res[name] = {"state": st, "u": u, "re": re, "nu": nu, "f": ff, "h": h, "dp": dp,
                             "C": stream.mass_flow_kg_s * st.cp}
            A_m = 0.5 * (sides["hot"]["area_m2"] + sides["cold"]["area_m2"])
            R = (1.0 / (res["hot"]["h"] * sides["hot"]["area_m2"])
                 + t_mean / (req.material.thermal_conductivity_W_mK * A_m)
                 + 1.0 / (res["cold"]["h"] * sides["cold"]["area_m2"]))
            UA = 1.0 / R
            Ch, Cc = res["hot"]["C"], res["cold"]["C"]
            Cmin, Cmax = min(Ch, Cc), max(Ch, Cc)
            ntu = UA / Cmin
            eff = corr.effectiveness_counterflow(ntu, Cmin / Cmax)
            Q = eff * Cmin * (Th - Tc)
            Th_out, Tc_out = Th - Q / Ch, Tc + Q / Cc
            new_h, new_c = 0.5 * (Th + Th_out), 0.5 * (Tc + Tc_out)
            converged = abs(new_h - Th_m) < 0.01 and abs(new_c - Tc_m) < 0.01
            Th_m, Tc_m = new_h, new_c
            out = {"UA": UA, "ntu": ntu, "eff": eff, "Q": Q, "Th_out": Th_out, "Tc_out": Tc_out, "res": res}
            if converged:
                break
        else:
            warnings.append("property iteration did not converge to 0.01 K in 8 passes")

        res = out["res"]
        phase_change = 0.0
        for name, stream, T_out in (("hot", hot, out["Th_out"]), ("cold", cold, out["Tc_out"])):
            r = res[name]
            for c in (r["nu"], r["f"]):
                if not c.valid:
                    warnings.append(f"{name}: Re {r['re']:.0f} {c.regime}; " + "; ".join(c.notes))
            if r["dp"] > 0.1 * stream.inlet_pressure_Pa:
                warnings.append(f"{name}: pressure drop exceeds 10% of inlet pressure; incompressible treatment is doubtful")
            ph_in = fluids.phase(stream.fluid, stream.inlet_temperature_K, stream.inlet_pressure_Pa)
            ph_out = fluids.phase(stream.fluid, T_out, stream.inlet_pressure_Pa)
            if "twophase" in (ph_in, ph_out) or (ph_in != ph_out and "unknown" not in (ph_in, ph_out)):
                phase_change = 1.0
                warnings.append(f"{name}: phase change ({ph_in} -> {ph_out}); single-phase model invalid")
            if f_nu.calibrated and f_nu.basis and req.thermal_boundary not in f_nu.basis:
                warnings.append(f"{f_nu.key} was fitted against a different thermal boundary than "
                                f"{req.thermal_boundary}")
            for fac in (f_nu, f_f):
                if (fac.calibrated and "level-set resolution" in fac.basis
                        and f"level-set resolution {self.resolution};" not in fac.basis + ";"):
                    warnings.append(f"{fac.key} was fitted with Dh and porosity measured at a different level-set "
                                    f"resolution than this model ({self.resolution})")
            for fac in (f_nu, f_f, f_tau):
                if fac.calibrated:
                    for msg in fac.out_of_range(reynolds=r["re"], wall_ratio=ratio, prandtl=r["state"].pr):
                        warnings.append(f"{fac.key} used outside its fitted range: {msg}")
        warnings = list(dict.fromkeys(warnings))

        m = req.manufacturing
        mass = uc.solid_fraction * V * req.material.density_kg_m3
        dp_ratio_h = res["hot"]["dp"] / hot.max_pressure_drop_Pa
        dp_ratio_c = res["cold"]["dp"] / cold.max_pressure_drop_Pa
        cells_across = min(L, W, H) / a
        constraints = {
            "duty": (req.duty_W - out["Q"]) / req.duty_W,
            "dp_hot": dp_ratio_h - 1.0,
            "dp_cold": dp_ratio_c - 1.0,
            "min_wall": (m.min_wall_m - t_min) / m.min_wall_m,
            "neck_hot": (m.min_channel_m - sides["hot"]["neck_m"]) / m.min_channel_m,
            "neck_cold": (m.min_channel_m - sides["cold"]["neck_m"]) / m.min_channel_m,
            "isolated_pockets": max(sides["hot"]["isolated"], sides["cold"]["isolated"]) - ISOLATED_LIMIT,
            "cells_across": (req.min_cells_across - cells_across) / req.min_cells_across,
            "envelope": max(L / m.envelope_length_m, W / m.envelope_width_m, H / m.envelope_height_m) - 1.0,
            "single_phase": phase_change,
        }
        metrics = {
            "duty_W": out["Q"],
            "effectiveness": out["eff"],
            "ntu": out["ntu"],
            "UA_W_K": out["UA"],
            "hot_outlet_K": out["Th_out"],
            "cold_outlet_K": out["Tc_out"],
            "mass_kg": mass,
            "core_volume_m3": V,
            "wall_mean_m": t_mean,
            "wall_min_m": t_min,
            "cells_across": cells_across,
            "solid_fraction": uc.solid_fraction,
        }
        for name in ("hot", "cold"):
            r, s = res[name], sides[name]
            metrics[name] = {
                "pressure_drop_Pa": r["dp"],
                "velocity_m_s": r["u"],
                "reynolds": r["re"],
                "regime": r["nu"].regime,
                "nusselt_baseline": r["nu"].value,
                "friction_baseline": r["f"].value,
                "h_W_m2K": r["h"],
                "hydraulic_diameter_m": s["dh_m"],
                "neck_m": s["neck_m"],
                "wetted_area_m2": s["area_m2"],
                "fluid_fraction": s["phi"],
            }
        return Evaluation(
            params=dict(params),
            objectives={"mass_kg": mass, "dp_ratio": max(dp_ratio_h, dp_ratio_c)},
            constraints={k: (v if math.isfinite(v) else 1e9) for k, v in constraints.items()},
            metrics=metrics,
            warnings=warnings,
            uncalibrated=uncal,
        )
