# Rules for agents working on ACES

These rules apply to every human and AI contributor. Several are inherited from the simlab runtime
(`cudax-sim-runtime`, see its `docs/RUNTIME_PLAN.md`), where they were learned the hard way.

## Data and claims

1. **No fabricated or sample data** in any tool, report or UI. Empty states say what is missing.
   Test inputs live only in `tests/` and are labelled as test inputs.
2. **Properties must be sourced.** Fluids come from CoolProp. Solid properties come from the spec with a
   `source` field. Never add a default conductivity, density or strength.
3. **Fast tiers and surrogates rank; a registered solver signs off.** Nothing is quoted, printed or sold
   until simlab has verified it. Reports must say "not verified" until then.
4. **Uncalibrated means uncalibrated.** A calibration factor without provenance stays at 1.0 and is listed
   on every result that uses it. `Registry.set` refuses factors with no sources.
5. Correlations report their validity range. Never silently use one outside it.

## Numerics

6. **FP64 for every verification solve.** FP32 Jacobi-PCG falsely converges on stiff and slender problems
   (simlab finding, 2026-10-05).
7. Face loads are consistent tractions, not equal nodal forces.
8. Every simlab case declares `vram_gb`; anything over 60 GB is refused.

## Ownership, evidence and licences

9. ACES and its R&D records belong to **Carbon Project Australia Pty Ltd (CPA)**. Specs and ledger records
   with any other company are rejected. simlab's vocabulary for CPA is `carbon`.
10. Every design run appends to the hash-chained ledger. Never edit or delete ledger lines.
11. New dependencies need a licence entry in `docs/LICENCES.md` first. Excluded: FluidX3D, OpenSees,
    cuPyNumeric, DrivAerNet++ and any non-commercial licence. GPL/AGPL tools run only as separate processes.
12. Design data may be export-controlled (DSGL). Keep geometry on the LAN; classify before any transfer
    outside Australia.

## The rig

13. Never touch the CMP 170HX unlocker, drivers, kernel or CUDA on the rig host. Containers only.
14. Agents submit and read through simlab-mcp. Mode switching stays human, through GRID CONTROL.

## Working style

15. Tests first for physics: every formula gets a textbook or hand-calculated check.
16. Keep the model honest about scope. If a model ignores something (headers, maldistribution, entrance
    effects), it says so in its warnings.
