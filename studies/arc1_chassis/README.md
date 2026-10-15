# ARC-1 — robotic arc-lattice racing chassis

> **In ACES.** Imported from `C:\Users\shedm\arc-lattice-chassis` on 2026-10-06 as a design study. It is
> the reference for the `arc_lattice_rod_welding` manufacturing route: robot-placed filler rods with
> stud-welded nodes. It runs as the `rod.lattice` route from an ACES spec
> (`specs/templates/rod_lattice.yaml`, `aces rod SPEC --out DIR`), which works on a copy of this folder,
> checks the outputs and writes an evidence record.
>
> - Run from this folder: `pip install -e ../..[studies]`, then follow the regeneration order in section 10.
>   All results land in `out/`, which git does not track; regenerate rather than commit them.
> - `cad/arc1_envelope.stl/.step` are committed copies of the Fusion tub export. `packing.py` and
>   `foamfill.py` fall back to them when `out/` has no fresh export from `fusion_envelope.py`.
> - The `fusion_*.py` scripts run inside Fusion 360 (Scripts and Add-Ins), not from a terminal.
> - Materials, joint efficiencies and costs below are the study's own figures. Section 11 lists what
>   is assumed rather than measured.
> - **Correction (2026-10-06).** PyNiteFEA 3.2 reports compression as a *positive* axial force. `fea.py`
>   assumed the opposite, so compressed members were checked against the (higher) collared-joint
>   tension capacity. `fea.py` now measures the sign convention at start-up. Corrected peak
>   utilisation: rev A 1.85 (was 1.67), rotary 1.51 with 8 members over 1.0 (was 1.39). Mass and
>   torsional stiffness are unchanged.
> - **Rod-by-rod check and rev B (2026-10-06, ACES).** `scripts/rodcheck.py` maps the frame FEA forces of
>   every member onto its chords, battens and diagonals and checks each rod at its own length. It also
>   checks the bending-compression chord of members in net tension, which `fea.py` checked against the
>   tension capacity. Rev A: **1.68**, in 90 chord rods of the 6 links at the four supported lower front
>   pickups (2 g cornering; the links work in bending). The lacing peaks at 0.91 and does not fail.
>   `cad/params_revB.json` adds a second link per quadrant at `FLA_L_F` and `RLA_L_F` (and mirrors) and
>   uses 52 mm pickup booms. Bigger booms alone do not work: at 52 mm with one link per quadrant the
>   chords still reach 1.37 and the diagonals fail.
>
>   | | rev A | rev B |
>   |---|---|---|
>   | Rod-by-rod max utilisation | 1.68 (90 rods over) | **0.93** (none over) |
>   | Frame check, nominal 60 mm rods | 1.85 | 0.95 |
>   | Torsional stiffness | 2,284 Nm/deg | **2,790 Nm/deg** |
>   | Lattice mass | 42.4 kg | 47.4 kg |
>   | Machined hubs | 318 | 322 |
>   | Rods needing a fixture | 2,615 | 3,940 |
>   | Single-robot build | 46.7 h | 49.4 h |
>
>   Run either through ACES: `aces rod SPEC --out DIR` with `params: params_revB.json`. Rev A's own numbers
>   below are unchanged.

A single-seat formula/prototype chassis designed to be built by a 6-axis robot
placing discrete 2.4 mm filler rods and arc-welding each junction, exactly as
proposed. Everything here regenerates from `cad/params.json`.

**Fusion document:** `ARC-1 Chassis - rev A` (Default Project, saved to cloud)

---

## 1. The process question, answered

> *"couldn't it use rod … and eject it, and then spark an arc at each of the
> junctions … each of the junction points is essentially like a spot weld"*

Yes. One change makes it work far better than a TIG tack:

**Use drawn-arc / capacitor-discharge stud welding, not a TIG tack.**

| | TIG tack | Drawn-arc / CD stud weld |
|---|---|---|
| Cycle | 1–3 s | **3–6 ms** (CD), ~0.2 s (drawn arc) |
| Fusion | partial, one side | **full rod cross-section** |
| Heat into the growing lattice | high — cooks neighbours | **negligible** |
| Joint strength | ~60 % of parent rod | **≥ parent rod** |

The rod *is* the electrode. You touch it to the target, draw an arc, melt both
faces, plunge. It is a mature industrial process (that is how weld studs go
onto car bodies), it is fast, and the tiny heat input is what makes building a
thin lattice possible at all — a continuous TIG arc would distort the structure
it is standing on.

**Architecture:** each junction is a small WAAM-deposited **node ball** (7 mm),
and rods are stud-welded into it. Ball first, then rods.

**Material:** start with **316L**. Titanium needs full inert shielding — above
~400 °C it picks up oxygen and embrittles — so Ti-6Al-4V is phase 2 with a
local argon chamber or trailing shroud, not a drop-in swap.

---

## 2. The one number the whole design falls out of

A 2.4 mm rod buckles long before it yields. Setting slenderness `KL/r = 80`
(the inelastic/Euler transition) with `K = 0.8` and `r = d/4`:

```
L/d = 25        →  2.4 mm rod  →  60 mm strut
```

| strut length | compression capacity | vs tension yield |
|---|---|---|
| 40 mm | 1,170 N | 89 % |
| **60 mm** | **992 N** | **76 %** |
| 80 mm | 744 N | 57 % |
| 120 mm | 337 N | 26 % |

Past 60 mm it falls off a cliff. That ratio sets the cell size, the strut
count, the mass and the build time.

---

## 3. What did NOT work (and why it matters)

**A uniform lattice skin over the whole chassis is a dead end.** The first
attempt — a 60 mm sandwich lattice wrapped over the tub surface — came out at
50,489 struts, 2,972 m of rod, **107 kg**. That is ~10 kg/m² of surface: a
superb *panel*, and a useless *chassis*. A chassis is discrete load paths, not
continuous skin.

**The fix is hierarchical.** Conventional space-frame topology, but every
member is a **3-chord micro-truss boom** of 2.4 mm rods instead of a steel tube:

| boom R | equal-I steel tube | boom mass | saving |
|---|---|---|---|
| 25 mm | 20.5 × 1.6 | 0.474 kg/m | **38 %** |
| 30 mm | 23.0 × 1.6 | 0.499 kg/m | **42 %** |
| 34 mm | 25.3 × 1.6 | 0.525 kg/m | **45 %** |

**Torsion is the trap.** A laced boom carries torque only through its
diagonals. Calibrated against a directly-solved rod model, its effective J is
only **10–14 %** of a closed section with the same bending inertia. Assuming
`J = 2I` would have overstated chassis stiffness several-fold.

---

## 4. Structure as built

| | |
|---|---|
| Macro frame | 107 nodes, 356 lattice-boom members, 93.1 m of boom |
| Micro lattice | **5,603 nodes, 16,273 rods, 950 m of 2.4 mm rod** |
| Chassis mass | **42.4 kg** (34.4 kg rod + 8.1 kg node balls) |
| Torsional stiffness | **2,284 Nm/deg** |
| Max member utilisation | **1.85** (1.67 before the 2026-10-06 sign fix) — all 6 over-unity members are pickup brackets, not lattice |
| Load cases | 3 g bump, 1.5 g braking, 2 g cornering, 1,000 Nm torsion rig |

Loads are **distributed inertial mass reacted at the four tyres**, not point
loads at the pickups — applying "bump" at the same four points you support
turns the analysis into a nose-cantilever test and inflates member forces ~10×.

> **Note on the two mass figures.** The macro model estimates 38.1 kg from
> boom mass-per-metre; the rod-by-rod count is 42.4 kg. The +11 % is real —
> subdividing each member into whole 60 mm bays adds partial bays the
> per-metre formula cannot see. **42.4 kg is the honest number.**

---

## 5. vs a carbon monocoque

**Read the basis before the number.** 42.4 kg is the welded lattice only:

| in the 42.4 kg | |
|---|---|
| pure tub structure | 35.26 kg |
| roll hoops + braces | 1.49 kg |
| suspension pickup link structure | 5.67 kg |

| NOT in the 42.4 kg | |
|---|---|
| 318 machined hub nodes (counted as 1.44 g balls; a 16 mm hub is 17.2 g) | +5.0 kg |
| spherical bearings + housings, 16 pickups | +1.9 kg |
| module joint flanges + bolts (3 joints) | +2.4 kg |
| rockers ×4 | +0.9 kg |
| **comparable complete structure** | **≈ 52 kg** |

Aero is excluded here, and excluded from the benchmarks too, so that much is
like-for-like. (For reference: ~5.1 m² wetted at typical CFRP areal density is
**≈13 kg** for the whole aero package.)

| | mass | torsion | **specific** |
|---|---|---|---|
| Steel tube spaceframe (FSAE class) | ~32 kg | ~1,300 Nm/deg | ~41 Nm/deg/kg |
| **ARC-1 (hubs + inserts + joints in)** | **~52 kg** | **2,284 Nm/deg** | **~44 Nm/deg/kg** |
| Carbon monocoque + steel hoops | ~34 kg | ~3,200 Nm/deg | ~94 Nm/deg/kg |

**ARC-1 is level with a steel spaceframe, not ahead of it.** Published FSAE
frames scatter from ~29 to ~67 Nm/deg/kg depending on how well they are
designed; 44 sits inside that scatter, so **the advantage over welded steel
tube is not established.** An earlier draft of this document claimed ~30 %
better by using the 42.4 kg figure, which omits the hubs and inserts.

There is ~10 kg of identified headroom (see §5a) which would take it to ~42 kg
complete and ~54 Nm/deg/kg — genuinely ahead of steel tube, still well short of
carbon.

**The gap to carbon will not close**, because it is in the material, not the
geometry:

| | E/ρ (GPa per g/cm³) |
|---|---|
| 316L steel | 24.1 |
| Ti-6Al-4V | 25.7 |
| CFRP (UD, on-axis) | **84.4** |

A lattice wins by putting material at the optimal radius. That is worth ~30 %.
It cannot recover a 3.5× material deficit.

**Titanium barely helps.** Same geometry in Ti-6Al-4V gives 55 % of the mass
but 59 % of the stiffness — a net specific-stiffness gain of only **7 %**,
because Ti and steel have almost identical E/ρ. Titanium is a *strength*
material, not a stiffness material. Use it at the pickups, not the skin.

**Where ARC-1 actually wins:** no mould, no autoclave, no tooling lead time;
damage is visible and repairable *by the same robot*; fully recyclable; heat
tolerant; and it can be built in a shed with one HC10DTP. The case for it is
manufacturing economics and repairability, **not** specific stiffness.

### 5a. Where the mass comes back out

Identified, not yet claimed — the figures above stand until this work is done.

| lever | saving | note |
|---|---|---|
| Node balls 7 mm → 6 mm | ~3.5 kg | 8.05 kg (19 % of the chassis) sits in 5,603 balls — the single biggest lever in the process |
| Rationalise macro node valence | ~4 kg | the 318 machined hubs exist because too many booms converge at one point; fixing that also fixes the sub-25° weld access |
| Thin the diagonals | ~2 kg | 8.17 kg at peak utilisation **0.62** — the least-loaded class carries the most mass |
| Fully inboard suspension | ~1.5 kg | 5.67 kg of pickup links over 48 members |

### 5b. Carbon-wrapped variant — the skin earns its mass

Bonding a carbon skin to the lattice is not bodywork, it is structure: it
closes the shear panels the lattice currently braces with diagonals. Modelled
by replacing each bay panel with its equivalent cross-brace
(`A = t·G·d³ / 2·a·b·E`) and re-solving:

| | bare | carbon-wrapped |
|---|---|---|
| Torsional stiffness | 2,284 Nm/deg | **3,944 Nm/deg (+73 %)** |
| Structural skin | — | 2.89 m² @ 0.75 mm = **4.9 kg** |
| Full shell (+ nose, ends, cosmetic) | — | 4.93 m² = 8.4 kg |
| Complete structure | ~52 kg | **~60 kg** |
| **Specific stiffness** | ~44 Nm/deg/kg | **~66 Nm/deg/kg** |

That closes most of the gap: **66 vs ~94** for a carbon monocoque, and the
wrapped lattice actually has *higher absolute* torsional stiffness than a
typical FSAE monocoque (3,944 vs ~3,200) — at ~26 kg more mass.

**Three things that will bite, and are not in the number above:**

1. **Bond area.** The equivalence assumes a fully effective skin. A skin bonded
   to a *rod* lattice has line contact along rods, not continuous area — so the
   real gain will be *below* +73 %. Needs a bonded-panel coupon test.
2. **Galvanic.** Carbon fibre is strongly cathodic to stainless. In contact,
   with any moisture, the 316L corrodes. A glass/epoxy isolation ply between
   skin and lattice is mandatory, not optional.
3. **CTE mismatch.** CFRP ≈ 0–2 µε/°C, 316L ≈ 16 µε/°C. Over the 2.6 m chassis
   through a 60 °C swing that is **~2.3 mm of differential movement** — enough
   to fail a rigid bond line. Use a toughened/compliant adhesive, or segment
   the skin so no single bonded panel spans the whole length.

### 5c. Polyurethane foam fill — it does not do what you'd expect

`python scripts/foamfill.py`. Three separate questions, three different answers.

**It adds no stiffness.** E_foam/E_316L is 0.001–0.014 %. Filling the lattice
to make it stiffer does not work and never will.

**It destroys the buckling limit, which is far more valuable.** A rod embedded
in foam is a *column on an elastic foundation*: its critical load becomes
`P_cr = 2√(EI·k)` — **independent of length**.

| PU density | E (MPa) | P_cr | vs rod yield | buckle half-wave |
|---|---|---|---|---|
| free 60 mm rod | — | 992 N | 76 % | — |
| 16 kg/m³ | 2.5 | 1,773 N | **100 %** | 59 mm |
| 32 kg/m³ | 8.0 | 3,171 N | **100 %** | 44 mm |
| 64 kg/m³ | 26.0 | 5,717 N | **100 %** | 33 mm |

Even the lightest foam takes the rod past its own yield load. Embedded, every
rod is **yield-limited at any length** — `L/d = 25` stops applying. (Partial
embedment still works: at half the foundation modulus a 32 kg/m³ fill still
gives 2,242 N, so foam contacting one side of a rod is enough.)

**What that buys, at a 120 mm bay:**

| bay | rods | steel | + foam @32 | total |
|---|---|---|---|---|
| 60 mm (current) | 16,273 | 42.4 kg | 10.8 | 53.2 |
| **120 mm** | **10,848** | **30.5 kg** | 10.8 | **41.3** |

**Roughly mass-neutral.** The gain is not weight — it is **5,425 fewer rods**:
14 h less robot time, and 5,425 fewer welded joints to fatigue-crack. Foam
attacks the single biggest open risk in the design (§11).

**Selective fill — 8.1 kg for the unambiguous wins:**

| where | volume | mass | why |
|---|---|---|---|
| Nose / impact attenuator | 28 L @ 64 | 1.8 kg | foam + lattice is an excellent crushable attenuator |
| Backing the bonded carbon skin, 25 mm | 123 L @ 32 | 3.9 kg | stops local skin buckling so the skin develops full shear — this is what makes §5b's +73 % real |
| Floor sandwich, 60 mm | 74 L @ 32 | 2.4 kg | closed, low inspection need, adds underbody shear |

**Why not fill all of it yet:**

- It **buries 5,603 welded joints you cannot inspect**, on a structure with no
  fatigue data. That is the wrong order to do things in.
- It kills the repairability argument, which was ARC-1's main advantage over
  carbon in the first place.
- Moisture trapped against 316L in a cell you can never drain is a crevice
  corrosion trap.
- PU degrades above ~100–120 °C — keep it away from brakes, motor, inverter.
- PU burns and emits HCN. Fire-retardant grade only, near a driver.

**Sequencing:** build rev A unfilled, fatigue-test it, *then* foam-fill
production cars at a longer bay once the joint data exists.

---

## 6. Suspension — inboard pushrod, generatively designed

Inboard pushrod-actuated rocker, spring/damper inside the tub. Chosen because
the frame FEA found its only overloaded members were outboard cantilevered
pickups — going inboard moves those loads into well-triangulated structure.

**Fusion's Generative Design workspace has no API** (confirmed: no GD classes
exist in the API surface), so the rocker was optimised here instead — real SIMP
topology optimisation with density filtering, optimality-criteria updates,
three load cases and passive solid/void regions.

| | |
|---|---|
| Pushrod load | 3,534 N bump / 5,477 N kerb / −1,237 N rebound |
| Motion ratio | 0.783 |
| Rocker | **225 g** in 12 mm 7075-T6, 80.1 cm³, 66.7 cm² plan |

> The first optimisation **deleted the damper arm entirely** — because I had
> grounded both the pivot *and* the damper eye, so the optimiser only needed
> one path to ground and took the cheaper one. A rocker is a lever: the pivot
> is the fulcrum, the damper is a reaction *force*. Fixing that produced the
> connected three-boss part.

---

## 7. Aerodynamics

NACA 4-digit sections, inverted, blunt trailing edge (a cusped TE will not
loft — and cannot be manufactured anyway).

| surface | | downforce @ 150 km/h |
|---|---|---|
| Front wing | 2-element, 1400 mm span | 1,099 N |
| Rear wing | 2-element, 1000 mm span, on pylons | 1,198 N |
| Diffuser | 560 mm wide, 680 mm long, 4 strakes | 776 N |
| **Total** | | **3,073 N = 1.01 g** |

Balance 45 % front. Plus sidepod radiator ducts and an airbox behind the main
hoop. Scaling: 0.45 g at 100 km/h, **1.80 g at 200 km/h**.

---

## 8. Packaging — motors and batteries

Measured by voxelising the interior and subtracting every rod plus 8 mm
clearance. **212 L free internal volume.**

| bay | free | largest clear box |
|---|---|---|
| nose / attenuator | 12.8 L | 144 × 240 × 48 |
| footwell / pedals | 40.5 L | 336 × 228 × 120 |
| cockpit | 48.0 L | open trough (crossed by ties) |
| **engine / battery** | **90.8 L** | **276 × 384 × 216 mm** |
| gearbox / rear | 19.9 L | 264 × 180 × 108 |

**You do not need hub motors.**

| | |
|---|---|
| Emrax 188 / 208 / 228 | fits |
| **Emrax 268 (≈200 kW, 20.3 kg)** | **fits inboard** |
| 2 × Emrax 228 side by side | does not fit |
| 7 kWh pack as one box | does not fit |

A single inboard motor up to ~200 kW fits comfortably. The **battery does
not** fit as a single block and must be distributed — floor, sidepods, nose.
That is standard practice and better for CG anyway; the sidepod ducts (20.9 L
each) become battery bays on an EV.

Hub motors would free the bay but add ~10 kg of unsprung mass per corner,
which is the wrong trade for a car with 1 g of aero.

---

## 9. Build plan

| | |
|---|---|
| Rod cut list | **56 distinct lengths**, 30–86 mm — 8 lengths cover most of it |
| Most common | 59 mm ×1,661, 38 mm ×1,585, 55 mm ×1,300 |
| Nodes robot-weldable | **5,285 (94.3 %)** |
| Nodes needing a machined hub | 318 (5.7 %) — too crowded or under 25° torch access |
| Rods placeable bottom-up | 13,658 (83.9 %) |
| Rods needing a temporary fixture | 2,615 (16.1 %) |
| Cell time, 1 robot | **47 h** (42 h rods + 5 h node balls) |
| Cell time, 4 robots | ~12 h |

Modules split at x = 650 / 1300 / 1950. Module 2 is 1058 mm tall because of
the main roll hoop — beyond a 650 mm build cube, accepted per direction, so the
cell needs either a taller working envelope or a rotary positioner.

---

## 8b. rev A-rotary — the chassis rebuilt around the bare 13B block

`cad/params_rotary.json`, `out_rotary/`, Fusion doc **ARC-1 Chassis - rev
A-rotary**. Identical to rev A except the rear deck, so the delta is
attributable.

**Bare block vs bare motor — no auxiliaries:**

| | mass | volume | peak | kW/kg | kW/L |
|---|---|---|---|---|---|
| 13B-REW core | ~125 kg | 380×400×450 = 68 L | 206 kW | 1.65 | 3.0 |
| Emrax 268 | 20.3 kg | ⌀268×91 = 5.1 L | 200 kW | **9.85** | **39** |

The 13B's compactness claim holds against **piston** engines — a 2.0 T4 of the
same output is ~135 kg bare and taller with its accessory drive. Against an
electric motor it is 6× the mass and 13× the volume.

**Three changes were needed to make the block fit:**

1. **Rear deck grown** to a sports-prototype section: stations at x=1950/2300/
   2600 go to half-width 335/330/280 and height 790/775/620 mm.
2. **Engine-bay bulkheads opened** (`open_bays: [[1900, 2450]]`) — ring,
   longerons and a floor tie only. You cannot run an X-brace through the middle
   of an engine bay, and the first attempt did exactly that: it halved the
   usable bay.
3. **Dry sump.** The wet-sump core is 450 mm tall and does **not** fit. Without
   the pan it is 370 mm and does. Race rotaries are dry-sumped anyway.

**Cost of the rotary rear:**

| | rev A (EV) | rev A-rotary | delta |
|---|---|---|---|
| Chassis mass | 42.4 kg | **45.0 kg** | +2.6 |
| Torsional stiffness | 2,284 Nm/deg | **2,224 Nm/deg** | −60 (−2.6 %) |
| Rods | 16,273 | 17,208 | +935 |
| Max member utilisation | 1.85 | **1.51** (8 members over 1.0) | better (both corrected 2026-10-06; were 1.67 / 1.39) |
| Engine bay clear box | 276×384×216 | **432×408×360** | +150 % volume |
| Robot-weldable nodes | 94.3 % | 95.0 % | — |

**+2.6 kg and 2.6 % of torsion to take a 13B.** That is cheap, and the bay is
now big enough for twin Emrax 228s or a 7 kWh accumulator too — so the same
rear works for either powertrain.

| fits the 432×408×360 bay | |
|---|---|
| 13B-REW core, **dry sump** (380×400×370) | **yes** |
| 13B-REW core, wet sump (450 tall) | no |
| 13B Renesis core, wet sump (440 tall) | no |
| Emrax 268 / 2× Emrax 228 / 7 kWh pack | yes |

---

## 8a. 13B rotary as an alternative to the EV powertrain

`python scripts/rotary.py`. Engine figures are typical published values for a
built unit — **weigh and measure your actual engine**, because the engine mass
is what drives the whole comparison.

**It does not fit as drawn.**

| | core L×W×H | dressed | peak |
|---|---|---|---|
| 13B-REW twin turbo | 380 × 400 × 450 | 165 kg | 206 kW |
| 13B-MSP Renesis NA | 370 × 390 × 440 | 145 kg | 175 kW |
| 13B 6-port NA | 370 × 390 × 430 | 140 kg | 120 kW |

The measured bay is **276 × 384 × 216 mm**. A 13B core is ~450 mm tall before
manifolds. Nothing in the family fits — the bay needs to roughly **double in
height**; budget **550 × 500 × 550 mm** with manifolds and oil pan. That is a
rear-end redesign, not a packaging tweak.

**Mass crossover.** Rotary fixed mass (engine + gearbox + cooling + exhaust +
intake + ECU + mounts + dry tank) is **277 kg** against **52 kg** for the EV
driveline — a 225 kg deficit before any energy is carried. Petrol then claws
it back, because 1 L of fuel at 24 % BTE beats 15 kg of lithium:

| useful kWh | rotary | EV | lighter |
|---|---|---|---|
| 10 | 280 kg | 126 kg | **EV** |
| 20 | 284 kg | 200 kg | **EV** |
| 30 | 287 kg | 275 kg | **EV** |
| 40 | 290 kg | 349 kg | **rotary** |
| 60 | 297 kg | 497 kg | **rotary** |

**Crossover at ~32 kWh useful.** A 20-minute sprint at ~80 kW average needs
~27 kWh — right on it. (At a 135 kg engine the crossover moves to ~28 kWh, so
the conclusion is not sensitive to that uncertainty.)

**Why it suits *this* chassis specifically:**

- **No reciprocating mass.** Primary and secondary shake are essentially nil. A
  four-cylinder's 2nd-order vertical shake is the classic killer of welded tube
  frames — and this structure has **5,603 fatigue-critical nodes with no
  fatigue data**. The smoothest available prime mover is worth real money here.
- **316L does not care about heat.** A carbon monocoque beside a rotary exhaust
  needs serious shielding; the lattice does not. This is one of the few places
  the metal structure genuinely beats carbon.
- Compact and low — good CG for a mid-mounted bay.

**Why it fights the last two things we added:**

- Rotary EGT is **900–1000 °C**. Epoxy Tg is ~120 °C and PU foam degrades from
  ~110 °C. The bonded skin (§5b) and the foam fill (§5c) must be kept out of
  the exhaust bay entirely, or get a shield plus an air gap. Hard constraint.
- Heat rejection to coolant *and* oil is high — two oil coolers is normal, and
  that is frontal area you pay for in aero.
- Apex seals are a consumable at sustained track power.

**Verdict.** Power density: 13B-REW 1.25 kW/kg dressed vs Emrax 268 at
9.85 kW/kg — the motor is ~8× better, but you must carry the battery. For a
**sprint car under ~25 kWh the EV is lighter and simpler**, and it keeps the
foam and bonded skin viable everywhere. For **endurance or long track days
above ~30 kWh the rotary wins on mass**, and its smoothness is a genuine gift
to a welded lattice. Either way the rear of the chassis gets redesigned.

---

## 8c. Aluminium, and whether this suits a UAV airframe

`python scripts/uav_alloy.py`. Short answer: **yes, and better than it suits
the car** — because the governing metric changes.

**The car analysis used the wrong metric for this question.** When strut
geometry is fixed, what matters is E/ρ, and every structural metal is within a
few percent of every other (which is why titanium gained only 7 %). When you
are free to re-size the rod — which you are, on a clean-sheet airframe — the
buckling-critical metric is **ρ/√E**:

| | E/ρ | **√E/ρ** | vs 316L |
|---|---|---|---|
| 316L stainless | 24.1 | 1.737 | 1.00× |
| 6061 | 25.5 | 3.074 | **1.77×** |
| 5083 | 26.4 | 3.152 | **1.82×** |
| Ti-6Al-4V | 25.7 | 2.410 | 1.39× |
| CFRP UD (benchmark) | 84.4 | 7.262 | 4.18× |

**Same strut capacity (992 N over 60 mm), re-sized per material:**

| | dia | L/d | mass | vs 316L |
|---|---|---|---|---|
| 316L | 2.40 mm | 25.0 | 2.171 g | 1.00× |
| 6061, welded HAZ | 3.43 mm | 17.5 | 1.501 g | **0.69×** |
| 6061 + post-weld heat treat | 2.88 mm | 20.8 | 1.058 g | **0.49×** |
| CFRP (benchmark) | — | — | 0.446 g | 0.21× |

Aluminium needs a *fatter* rod (lower E) but is light enough that the strut
still comes out 31 % lighter. Note `L/d = 25` was specific to 2.4 mm 316L — it
is not a universal constant.

**The weld-softened HAZ mostly does not bite.** With 60 mm struts and a ~25 mm
HAZ at each end, *every strut is entirely heat-affected* — there is no parent
material anywhere in the lattice. In a strength-critical weldment that is
fatal. Here it is survivable, because **buckling depends on E, and E is
indifferent to temper**. Only the short-column (Johnson) end of the curve
loses, worth the ~30 % between the two rows above.

And at UAV scale, **post-weld solution-treat + age is actually practical** — a
1 m airframe fits in a furnace, where a 2.6 m car chassis would distort on the
quench. That option simply did not exist for the chassis.

**A representative tail boom, 1.2 m, matched to a 30 × 1 mm CFRP tube
(0.146 kg/m):**

| boom | R | chord | kg/m | vs CFRP |
|---|---|---|---|---|
| 316L lattice | 40 mm | 1.62 mm | 0.233 | 1.60× |
| **6061 lattice** | 40 mm | 2.72 mm | **0.162** | **1.11×** |

**Within 11 % of carbon on a stiffness-driven boom** — a completely different
result from the chassis, where the lattice lost heavily to carbon. The reason
is that a boom lets you put material out at R = 40 mm, and the lattice's
geometric efficiency finally gets to pay for the material deficit. (The HAZ and
PWHT rows are identical here because the boom is sized by *stiffness*, so yield
never enters.)

**Where it genuinely suits a UAV — and where it does not:**

- **Good:** internal fuselage trusses under a skin, wing spars and ribs inside
  the aerofoil, integrated fittings and mounts, and no tooling at all — which
  matters more for UAV iteration than for a one-off car.
- **Bad:** an *exposed* boom. An 80 mm open truss has far more drag than a
  30 mm tube. The lattice belongs inside a fairing or inside the skin, not in
  clean air.

**Process reality — aluminium is harder to stud weld than steel:**

- Al₂O₃ melts at 2072 °C, the aluminium beneath it at 660 °C. The oxide must be
  disrupted: **argon shielding is mandatory**, where steel CD stud welding can
  often run in open air.
- Aluminium conducts heat ~4× faster — higher current, tighter timing window.
- Thermal expansion is ~2× steel's, so a growing lattice distorts more and the
  build sequence has to manage it.
- CD stud welding of aluminium is commercial but mostly ≤ 8 mm studs. A 3–4 mm
  rod is inside that range — this is not exotic.

**The real risk is fatigue, not strength.** Aluminium has no endurance limit,
and a UAV has continuous prop or engine excitation across thousands of welded
nodes. That needs a fatigue spectrum and coupon testing, not a static check.

### 8d. Wing skin, and a turbojet UAV

`python scripts/wing_skin.py`.

**A PFA skin and a 6061 lattice fight each other.** PFA consolidates at
350–400 °C. 6061 over-ages from ~200 °C and anneals at ~415 °C. Consolidating
the skin *is* an annealing cycle:

| 6061 temper | fy | rod dia | mass | vs 316L | governed by |
|---|---|---|---|---|---|
| T6 | 276 MPa | 2.88 mm | 1.058 g | 0.49× | buckling |
| T6 + weld HAZ | 125 MPa | 3.43 mm | 1.501 g | 0.69× | yield |
| **O (annealed)** | 55 MPa | 4.87 mm | **3.017 g** | **1.39×** | yield |

Unlike the weld HAZ, this one is **not** survivable. Annealed, the strut flips
from buckling-governed to **yield**-governed — and a yield-governed aluminium
strut is *heavier than the stainless one it replaced*. The entire aluminium
advantage is processed away.

**Two more problems with PFA as a structural skin:**

| system | G (shear) | cure | ΔCTE vs Al | growth over 1.5 m wing |
|---|---|---|---|---|
| PFA neat | 0.2 GPa (0.7 %) | 380 °C | +116 | **+69.8 mm** |
| PFA / glass | 2.5 GPa (8.3 %) | 380 °C | +36 | +21.8 mm |
| Epoxy / glass ±45 | 6.0 GPa | 177 °C | −10 | −2.8 mm |
| Epoxy / carbon ±45 | 30 GPa (100 %) | 177 °C | −21 | −6.1 mm |

A wing skin's job is carrying **torsion and shear**, so G is the number. And
PFA has ~18–20 mN/m surface energy — it is used *as a release film* because
nothing sticks to it. An unbonded skin contributes zero stiffness (on the car,
the bonded skin was worth +73 %; unbonded it would have been worth nothing).

**The elegant alternative:** 6061-T6 ages at 160 °C/18 h or 177 °C/8 h. **177 °C
is also the standard aerospace epoxy prepreg cure.** Solution-treat and quench
the bare lattice, lay up the skin, then **co-cure the skin and age the lattice
in one oven cycle** — full T6 and a bonded structural skin from a single
operation, no autoclave.

#### Turbojet changes two things

**(a) Temperature zoning.** 6061 holds 85 % of its yield at 150 °C, 60 % at
200 °C, 20 % at 300 °C. A small turbine runs 550–750 °C EGT and skin near the
exhaust sees 200–400 °C — aluminium is finished there. **The arc-lattice
process does not care: same robot, same arc, different rod.** Zone it:

| zone | rod |
|---|---|
| cool wing / fuselage | 6061 or 5083 |
| engine bay, tailpipe | 316L or Ti-6Al-4V |
| transition | stainless, bolted or bi-metal |

**This is where PFA earns its place** — as the skin over the *hot* zone
(260 °C continuous) where epoxy cannot go. Not as the structural skin of the
cool wing.

**(b) Rod resonance — the one that would bite hardest.** A small turbojet spins
100,000–150,000 rpm = **1,667–2,500 Hz**. First bending mode of one pinned
6061 rod:

| L | d | f₁ | |
|---|---|---|---|
| 40 mm | 3.43 | 4,253 Hz | above band |
| 50 mm | 3.43 | 2,722 Hz | above band |
| **60 mm** | **3.43** | **1,890 Hz** | **IN BAND** |
| 60 mm | 4.87 | 2,684 Hz | above band |
| 80 mm | 3.43 | 1,063 Hz | below band |

The 60 mm rod sits **inside the turbine's operating band**. At 2,000 Hz you
accumulate 10⁷ cycles in **1.4 hours** — an entire high-cycle fatigue life in
one afternoon's flying, and aluminium has no endurance limit to stop at.

Fixes, in order: **detune** (40 mm rods clear the band entirely), damp (foam
fill raises f₁ and adds loss — but keep PU out of the hot zone), isolate the
turbine on soft mounts. Run a modal survey on a real module before flying it.

### 8e. Furan resin — the right question

`python scripts/furan.py`. (§8d above answers PFA *fluoropolymer*, which is a
different material entirely — furfuryl-alcohol thermoset vs PTFE family.)

**The objection that killed PFA does not apply.** Furan cures at 150–180 °C,
so the co-cure trick survives intact: age the 6061 lattice to T6 and cure the
skin in the same oven cycle. No annealing, no lost temper.

| system | E | G (±45) | cure | service | ILSS | 6061 after |
|---|---|---|---|---|---|---|
| Epoxy / carbon ±45 | 60 GPa | 30 GPa | 177 °C | 140 °C | 75 MPa | T6 + ages it |
| **Furan / carbon ±45** | 58 GPa | **24 GPa** | 165 °C | **200 °C** | **38 MPa** | T6 + ages it |
| Epoxy / glass ±45 | 22 | 6.0 | 177 | 140 | 70 | T6 + ages it |
| Furan / glass ±45 | 21 | 5.0 | 165 | 200 | 35 | T6 + ages it |
| PFA / glass (ref) | 15 | 2.5 | 380 | 260 | 12 | **O (annealed)** |

Furan gives **80 %** of epoxy's shear stiffness and about **half** its
interlaminar shear strength. Fibre-dominated tension is much the same; it is
the *matrix-dominated* numbers that give ground — and a wing skin carries
shear. Budget ~20 % less torsional contribution, and watch fittings and
bearing points.

**Fire is where it is in a different class:**

| | LOI % | char @ 800 °C | |
|---|---|---|---|
| Epoxy | 22 | 8 % | burns, drips, heavy smoke |
| **Furan** | **50** | **55 %** | chars to a carbon insulator, very low smoke |
| PFA | 95 | 2 % | will not burn, but evolves HF above 400 °C |

55 % char yield means it forms a carbon insulator exactly where the heat is —
a genuine ablative barrier around a tailpipe, which no epoxy will do.

**The problem: acid catalyst against aluminium.** Furan cures with strong
sulfonic acids (pTSA and relatives) and residual acidity pits aluminium. Furan
FRP is conventionally kept *off* aluminium and mild steel for this reason. On a
lattice it is worse than on a plate structure: **every one of ~6,000 welded
nodes is a crevice**, crevices are where acidic pitting starts, and you will
never inspect inside one. Mitigations in order — put furan only on the
316L/Ti hot-zone lattice; or a glass veil plus neutral barrier coat between
resin and metal; or a neutralised/latent-catalyst system with a post-cure wash.

Also real: the **condensation cure releases water**, so without vacuum *and*
pressure and a slow ramp you get 5–10 % voids against <2 % for epoxy — and
voids destroy the very shear strength the skin exists to provide.

**Verdict — zone the resin the way you zone the metal:**

| zone | lattice | skin |
|---|---|---|
| cool wing / fuselage | 6061 | **epoxy** — not worth the acid risk for a 20 % shear penalty |
| engine bay / tailpipe | 316L or Ti | **furan** — char barrier, 200 °C, low smoke, and the acid problem largely goes away on stainless |

One process, one robot, two rods, two resins.

**Feedstock.** Not bagasse — the **hemicellulose (C5 / xylan) fraction of Beema
bamboo**, separated by formicobio fractionation then dehydrated with sulphuric
acid to furfural. That route matters technically, not just commercially: an
H₂SO₄ dehydration step puts **residual sulphate and free acidity in play**, and
residual acidity is exactly what decides whether this resin can sit against
aluminium. It should be a measured datasheet line, not an assumption.

### 8f. Demonstrator — when the resin IS the product

`python scripts/demo_wing.py`. Fusion doc **ARC-1 Furan Demonstrator - wing
box**, `out_demo/`.

If the furan prepreg is the thing being sold, the design inverts: you cannot
confine it to a hot corner, and you cannot leave a corrosion question open on
the datasheet. Two decisions follow.

**Build it on a 316L lattice, not 6061.** It removes the only blocking
objection outright — nobody can claim the resin ate the structure — and it
costs nothing on a demonstrator where absolute mass is not the headline.

**Make the skin structural and publish the delta.** A 600 × 300 mm wing
torsion box, 18 % t/c, 316L lattice on 2.4 mm rod, 18 mm booms, 60 mm bays:

| skin | G | torsional stiffness | skin mass | vs bare |
|---|---|---|---|---|
| none (bare lattice) | — | 46,777 Nm/deg | — | 1.00× |
| Furan / glass ±45 | 5 GPa | 60,400 | 0.231 kg | 1.29× |
| **Furan / carbon ±45** | 24 GPa | **95,287** | **0.192 kg** | **2.04×** |
| Epoxy / carbon ±45 | 30 GPa | 103,013 | 0.187 kg | 2.20× |

**The datasheet number: 2.0× torsional stiffness from 0.16 m² of prepreg, and
within 7 % of epoxy/carbon.** That 7 % is much better than the ~20 % the
coupon shear data implies, because in a real box the lattice diagonals share
the shear path — the resin's matrix weakness is diluted at system level. That
is a genuinely favourable argument and it is worth making with a rigged part
rather than a coupon.

Article cost: **487 nodes, 1,592 rods, 72.6 m of 2.4 mm rod, ~4.1 h** on one
robot. It fits a single build station.

**Lead the demo with fire.** LOI 50 and 55 % char *on a loaded structure* is a
demonstration no epoxy prepreg can answer. Torsion-rig it, burn it under load,
re-rig it, and publish the retained stiffness. A metal lattice under a charring
skin keeps its load path while the surface turns to carbon — that is the
photograph.

**The acid spec that unlocks aluminium later** — publish these and aluminium
becomes an engineering choice rather than a reason to walk away:

- acid number (mg KOH/g) of the mixed resin
- residual sulphate (ppm) — direct consequence of the H₂SO₄ dehydration
- pH of an aqueous extract of the cured laminate
- filiform / salt-spray coupons on 6061, with and without a barrier veil, to a
  stated duration

### 8g. Where a furan-skinned lattice is genuinely the right answer

`python scripts/applications.py`.

**First, what it is not.** A lattice puts material at a radius, which pays only
where there is volume. In a thin panel there is none:

| cell | depth | kg/m² |
|---|---|---|
| 30 mm | 30 mm | 15.6 |
| 60 mm | 60 mm | 7.8 |
| *Nomex honeycomb + 2 skins* | | *2–4* |
| *1 mm aluminium sheet* | | *2.7* |

So **not flat panels** — not cargo liners, not rail interiors, not a battery
lid. That is honeycomb's home ground and the lattice is 4–10× heavier there.
It wins where there is **3D volume, geometric complexity, and the open interior
is doing a second job.**

**The stack nothing else has:** a metal skeleton that does not burn under a skin
that chars to 55 % carbon (load path survives the fire, the surface becomes its
own insulation); ~95 % open interior with **cell size free to vary spatially**;
no moulds, so geometry is free; electrically continuous metal.

**[A] Graded-depth acoustic liner — the strongest novel case.** A quarter-wave
resonator absorbs at *f = c/4d*. Honeycomb is stuck at one depth per panel, so
liners are narrowband. A lattice is not:

| cell depth | peak absorption |
|---|---|
| 20 mm | 4,290 Hz |
| 45 mm | 1,906 Hz |
| 100 mm | 858 Hz |
| 150 mm | 572 Hz |

Grading 20→150 mm covers **570–4,290 Hz in one part** — the whole
turbomachinery tone range, broadband — in a designated fire zone, so the furan
is doing two jobs at once. This is the one thing here honeycomb *physically
cannot* do.

**[B]** Battery enclosure as a 3D space frame (not a panel) — lattice is crush
structure, cell retention and fire barrier at once. **[C]** Turbojet nacelle,
firewall, tailpipe shroud. **[D]** In-situ reinforcement and repair — the robot
grows a lattice onto existing structure; exploits the *process*, not the
material. **[E]** Pyrolysed carbon skin on a refractory lattice — furan is a
glassy-carbon precursor, so you get a carbon matrix without a months-long CVI
cycle (needs Inconel/310S, not 316L, at 800–1000 °C).

#### Space and harsh-environment construction

**The launch argument — feedstock packs 67:1.** The ARC-1 chassis is 353 L of
deployed structure made from **5.3 L of solid metal**. You cannot ship a mould,
a press or an autoclave. You can ship rod on a spool — the densest, dullest,
most damage-tolerant cargo there is. *This is the strongest argument the whole
process has, and it only exists off-planet.*

**The process gets simpler in vacuum, not harder.** CD stud welding makes its
own metal-vapour plasma from the electrode — no shielding gas, and vacuum is a
better shield than argon. One of very few joining processes that should work in
vacuum essentially unmodified. On Mars it is easier still: 6 mbar of CO₂ *is* a
shielding gas. And in microgravity even the 16 % of rods that needed a fixture
on Earth do not.

**Lattice as regolith formwork — shielding for free:**

| lattice depth | lattice kg/m² | regolith g/cm² | |
|---|---|---|---|
| 200 mm | 8.7 | 34 | useful |
| **500 mm** | **16.3** | **85** | **good GCR protection** |
| 800 mm | 24.1 | 136 | good |

98 % of that shielding mass never left the ground you are standing on. The
lattice is formwork, reinforcement and micrometeoroid standoff in one. (It is
*not* the pressure vessel — that is still a bladder.)

**What furan specifically brings up there:** NASA-STD-6001 flammability in a
sealed, oxygen-enriched volume is unforgiving, and LOI 50 with very low smoke
and toxicity is a qualification advantage, not a marketing line. And being a
glassy-carbon precursor means a pyrolysed skin is **atomic-oxygen resistant and
radiation-hard** — exactly what LEO destroys polymers for.

**What will bite:** LEO thermal cycling (−150 to +120 °C, ~16 cycles/day) gives
6.7 mm of CTE differential over 2 m between a 316L lattice and a furan skin —
the lattice is compliant enough, the *bond* is not. Atomic oxygen eats the skin
(coat it, pyrolyse it, or fly beyond LEO). Furan's condensation cure releases
water, so TML/CVCM needs a vacuum bake and a measurement before any promise.
And 16 thermal cycles a day is ~6,000 a year into every welded node — the same
unquantified fatigue risk as the chassis, with a worse duty cycle.

**Three horizons.** *Now:* the acoustic liner, testable in an afternoon.
*Next:* harsh environment on Earth — Antarctic, offshore, mining, remote
defence — same logic, available this year, and it pays for the space work.
*The reason to do any of it:* space, where 67:1 packing is an argument nobody
can answer with "just use honeycomb."

The through-line: **every application here is somewhere you cannot bring
tooling to the part.** That is the actual product — not the lattice and not the
resin, but structure without tooling, in places tooling cannot go.

---

## 9a. Full vehicle — mass and cost (carbon-wrapped EV)

`python scripts/budget.py --kwh 15`. Indicative AUD ex-GST, one-off, built
in-house with an owned robot cell. **±30 %.**

| | 8 kWh sprint | 15 kWh track |
|---|---|---|
| Dry mass, no driver | **280 kg** | **327 kg** |
| With 80 kg driver | 360 kg | 407 kg |
| Pack | 494 cells, 53 kg | 926 cells, 100 kg |
| Power/weight @200 kW | 555 kW/t | 492 kW/t |
| **Build cost** | **~$100,400** | **~$104,800** |

Mass by group (15 kWh): energy 100 kg, wheels+brakes 50 kg, powertrain 52 kg,
suspension 32 kg, structure 60 kg, aero 13 kg, systems 20 kg.

Cost by group: powertrain **30 %**, energy 19 %, chassis 14 %, suspension 12 %,
carbon 10 %, systems 8 %, wheels+brakes 7 %.

**Two things worth noticing:**

- **Going 8 → 15 kWh costs $4,320 and 47 kg.** At $10/cell the marginal money
  is trivial; the *mass* is the real price. Size the pack by lap time, not by
  budget.
- **$7,950 of the chassis cost is the 318 machined hub nodes** — 53 % of the
  chassis spend, for 5.7 % of the nodes. The same topology fix that removes
  ~4 kg (§5a) removes most of that cost. It is the highest-leverage change
  available in the whole design.

Not included: R&D iterations, spare tyre sets, transport, insurance, tooling
amortisation, the robot cell itself, or your labour.

---

## 10. Files

```
cad/params.json          single source of truth - regenerate everything from here
scripts/
  process.py             rod buckling -> the L/d = 25 design rule
  boom.py                lattice boom section properties + torsion calibration
  frame.py               macro space-frame topology
  fea.py                 Pynite structural analysis (rig + road models)
  micro.py               macro -> 16,273 rods, with process-rule enforcement
  topopt.py              SIMP topology optimiser (the generative engine)
  rocker.py              inboard rocker generative design
  aero.py                wings / diffuser / intakes + downforce
  packing.py             voxel packaging study (NOT packaging.py - that name
                         shadows the real `packaging` module matplotlib needs)
  buildplan.py           cut list, module split, build order, cell time
  budget.py              full vehicle mass + cost budget
  foamfill.py            PU foam fill: elastic-foundation buckling + trade
  rotary.py              13B rotary vs EV: fit, mass crossover, thermal
  uav_alloy.py           aluminium + UAV suitability: sqrt(E)/rho, HAZ, booms
  wing_skin.py           PFA vs epoxy skin, temper, CTE, turbojet resonance
  furan.py               furan resin skin: shear, fire, acid-vs-aluminium
  demo_wing.py           furan showcase demonstrator: wing torsion box
  applications.py        where the stack actually wins; space + harsh env
  fusion_envelope.py     lofts the tub in Fusion
  fusion_build.py        lattice mesh + skeleton + demo boom + rockers
  fusion_aero.py         aero surfaces
  fusion_render.py       presentation views
out/                     results, renders, STL/STEP
```

Regenerate in order: `process → boom → frame --dump → micro --mesh →
rocker → aero → packaging → buildplan`, then the Fusion scripts.

---

## 11. Honest gaps

- **Fatigue is unassessed.** 5,603 welded nodes is a lot of stress
  concentrations, and a race chassis is a fatigue environment. This needs a
  hot-spot/notch assessment and ideally coupon testing before anyone builds it.
- Joint efficiency (60 % bare, 95 % collared) is **assumed**, not measured.
  Pull-test real stud-welded rod-into-ball joints — this number propagates
  everywhere.
- Aero coefficients are handbook estimates, not CFD.
- The 5.7 % machined-hub nodes need designing; they are currently only counted.
- The mesh lattice in Fusion is a mesh body, not B-rep. Only the demo boom is
  solid — 16k rods of B-rep will not survive Fusion.
