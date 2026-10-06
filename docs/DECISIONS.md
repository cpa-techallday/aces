# Decisions

Newest first. Record the decision, who made it and why. Change a decision by adding a new row, not by editing
an old one.

| # | Date | Decision | By | Why |
| --- | --- | --- | --- | --- |
| D11 | 2026-10-06 | The laminar baseline is uniform wall temperature (Nu 3.66) unless the spec says otherwise | Engineering review | Conservative until calibrated; uniform heat flux is 19 % higher |
| D10 | 2026-10-06 | Manufacturing limits are checked against the **measured** minimum wall and channel neck, never nominal thickness or hydraulic diameter | Engineering review | The neck is 0.6–0.75 of Dh; nominal checks were non-conservative |
| D9 | 2026-10-06 | Calibration factors need provenance (`sources`); unsourced factors are refused on load and on set | Engineering review | An uncalibrated estimate must never look calibrated |
| D8 | 2026-10-06 | Each run directory carries `run.json` with its evidence-record hash. `export-simlab` verifies the record and the pareto.json sha256 before exporting. | Engineering review | Stops wrong RDTI attribution and post-hoc edits |
| D7 | 2026-10-06 | Specs and ledger records with a company other than CPA are rejected. simlab experiments use simlab's `carbon` tag. | Aaron (CPA owns ACES) | The sim campaign found specs tagged `omni` |
| D6 | 2026-10-06 | No default material properties. Fluids come from CoolProp; solids come from the spec with a `source`. | Aaron's standing rule: no fabricated data | Traceability |
| D5 | 2026-10-06 | Fast tiers and surrogates rank; a registered simlab solver signs off. Reports say "not verified" until then. | Inherited from simlab (decision 7) | Surrogate peaks under-predicted by 3.5–10.9 % in simlab tests |
| D4 | 2026-10-06 | FP64 for verification solves; vram_gb declared and capped at 60 GB | Inherited from simlab (decision 5) | FP32 PCG false convergence |
| D3 | 2026-10-06 | First domain: two-fluid TPMS heat exchangers | Aaron | Plan phase 1: billable sprints, cheap on-site testing |
| D2 | 2026-10-06 | Carbon Project Australia owns and sells ACES | Aaron | |
| D1 | 2026-10-06 | ACES is a separate repo that drives simlab through experiments and MCP. It does not fork `cudax-sim-runtime`. | Aaron | Avoids colliding with the server agent's edits to the runtime |
