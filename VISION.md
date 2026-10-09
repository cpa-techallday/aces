# ACES OS — vision and product plan

**Document:** ACES-OS-VISION-001 **Rev B** · **Date:** 2026-10-08 · **Owner:** Aaron (Carbon Project Australia / Omniscient AI) ·
**Written by:** Claude Code on DARKNESS. Rev A was a first synthesis from `research/FACT_PACK.md`; Rev B merges a planning
panel (an architect draft and a product draft, scored by three independent judges, who also listed factual corrections)
and Aaron's decisions of the same day. Every hard fact traces to the fact pack (cited as FP §n). Numbers are measured or
read from primary sources unless marked *(target)*, *(assumption)* or *(unverified)*. No customers, prices or
testimonials are invented. Appendix D lists what the panel changed.

---

## 0. The short version

**ACES OS is an engineering workstation operating system plus an on-premises GPU server.** It is Omarchy 4 "Quattro"
(Arch + Hyprland + Quickshell, agent-native, MIT) with the ACES platform built in: GPU simulation and generative design
on open solvers, the voxel and implicit CAD kernel, adapters into Fusion and Onshape, a creative suite (the seven ArtCraft
apps and an ACES video studio over LTX-2.5), native field integration (PLCs, robots, vehicle buses, lab devices,
radios), and an agent layer that drives all of it under one policy broker. Every run, tool call, approval and device
session lands in one tamper-evident ledger that doubles as R&D evidence.

What makes it a product rather than a configured laptop:

1. **Omarchy stays untouched.** ACES ships as signed `aces-*` packages, an ISO and container images built with Omarchy's
   own machinery (pacman channels edge → rc → stable, `omarchy-iso --local-source`, unattended `cidata` installs).
2. **The lane manifest is the product API.** One document, published by the desktop from the server's state, tells the
   shell, the router, the broker and every agent which model, simulation, video and device lanes exist right now.
   A human switches GPU modes in GRID CONTROL; everything else reconfigures itself.
3. **Project class drives policy.** `open`, `commercial`, `sovereign` and `controlled` projects get different routing,
   egress and approval rules automatically. Sovereign work never sees a cloud model.
4. **One ledger.** simlab's hash chain extends to tool calls, approvals, device transcripts, egress and updates. The
   evidence format and its verifier are open, so an R&DTI adviser can check a bundle without buying anything.
5. **Open core, paid operations.** The OS, plugins, adapters, kernel and verifier are open; revenue comes from
   fixed-fee design Programs first, then the signed channel with validated solver images, the Server (stock hardware,
   leased or licensed), support and the compliance pack. Hosted lanes come later.

**Decisions already made (Aaron, 2026-10-08):** DeepSeek-V4-Flash-0731 is the four-card model lane on GPUs 4–7, with
GLM-5.2 kept as the seven-card flagship mode; DARKNESS dual-boots Windows and ACES OS; the seven ArtCraft apps ship on
day one; the voxel kernel is standard in every install; GPU 6 is in the working set.

---

## 1. What it is, and why now

### 1.1 Positioning

To an engineering firm: *"A workstation and a server that turn your CAD into verified designs, manufacturing files,
test-bench results, reports and media, with the agents and the GPUs inside your own walls."*

To Aaron's existing framing: ACES is the self-hosted equivalent of LEAP 71's Noyron (physics-true generators, wide
sweeps on owned FP64 GPUs, surrogates, optimisers, verification on the real solver, evidence for every step; FP §2).
ACES OS is that platform made installable, operable by a team, and sellable. No parity with Noyron is claimed until
a hardware-validated design exists (FP §2: none yet).

### 1.2 Why Omarchy, why now

- **Omarchy 4 made the desktop programmable and agent-native.** One long-running Quickshell process hosts every visible
  piece as a plugin; coding agents are system citizens (lazy launchers for 13 agent CLIs, a usage panel, crash dumps
  handed to the agent, a shipped skill that lets an agent reconfigure the machine, hooks on system events); and the
  internals moved to pacman packages with an ISO builder and unattended installs (FP §4, §14). ACES adds packages
  instead of maintaining a distribution.
- **An MIT open-weight model that fits four 64 GB cards exists** (DeepSeek-V4-Flash-0731; FP §7), and the rig already
  runs vLLM and ik_llama.cpp lanes.
- **Open creative tooling arrived in one month:** LTX-2.5 (open weights, local fine-tuning, a first-party adapter family,
  `ltx-trainer`; FP §6, §14) and the seven ArtCraft apps (pure Rust, MIT/Apache-2.0, agent control channels, already on
  the AUR; FP §5).
- **The ACES platform exists** (FP §2): validated solvers, the queue and leases, the evidence ledger, the SIMLAB console,
  the generative loop, the Fusion bridge, formgen, the CAD skills.
- **The hardware is unusual in the right way:** about 90 TFLOPS FP64 across eight 64 GB cards (FP §3), which FEA and CFD
  want and AI boxes no longer provide.
- **Field tooling is Linux-native** (FP §15): the SDR, bus, controller and analysis tools engineers need are a `pacman -S`
  away, which Windows cannot match.

### 1.3 Who it is for

1. **Customer zero:** Aaron's entities (HERMES, the AERIS family kept internal, PRISM media, the robot cell).
2. **Australian engineering SMEs whose geometry cannot leave the LAN** (UAV, vehicles, RF, defence subcontracting) and who
   claim the R&D Tax Incentive *(assumption: the fact pack names no external customer)*.
3. **Additive and robotic-fabrication R&D firms**, reached through R&DTI advisers *(assumption)*.
4. Later: firms without GPUs, through hosted lanes.

---

## 2. Layering: what is Omarchy, what is ACES

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ACES Cloud (later): hosted model / sim / video lanes in a sovereign AU DC     │
├──────────────────────────────────────────────────────────────────────────────┤
│ ACES Server (the rig today; stock GPU hardware for customers)                │
│   GRID CONTROL :3200 modes (human-switched) · rig-state :8010 · simlab-mcp    │
│   :8020 · redis/rq + GPU leases · MLflow · validated solver images (digest)   │
│   model lanes (vLLM / ik_llama.cpp, OpenAI-compatible) · video lanes (LTX)    │
│   always-on services moved off DARKNESS: Nabu evidence API, qdrant, gateways  │
├──────────────────────────────────────────────────────────────────────────────┤
│ ACES OS (the desktop) = Omarchy + aces-* packages                             │
│   aces-base      fabric daemon, router, broker, ledger, skills, hooks, theme   │
│   aces-engineering  simlab client, gendesign, build123d, cad/dxf/gcode skills │
│   aces-voxel     implicit/voxel kernel, lattices (formgen), remeshing (standard)│
│   aces-cad       Fusion adapter (Windows VM lane), Onshape adapter             │
│   aces-creative  aces-artcraft-{photo,vector,film,light,pdf,effect,design}craft│
│   aces-studio    video studio client + LoRA registry client                    │
│   aces-field     device registry, aces-devd, bus/PLC/RF/analysis tool packages │
│   aces-consoles  web apps: GRID CONTROL, SIMLAB, MLflow, Video Studio, Nabu    │
│   aces-shell     aces.fabric widget, aces.approvals overlay, aces.ledger,      │
│                  aces.devices panels                                           │
├──────────────────────────────────────────────────────────────────────────────┤
│ Omarchy 4 Quattro (upstream, MIT, unmodified, installed from a pinned mirror  │
│   snapshot per ACES release): Arch · linux-omarchy · Hyprland · omarchy-shell │
└──────────────────────────────────────────────────────────────────────────────┘
```

| Layer | Ships as | Moves by |
|---|---|---|
| Omarchy 4 | upstream packages from a **pinned LAN snapshot of the Omarchy mirror** (including `quickshell-git`) | ACES advances the pin per release |
| ACES OS | `aces-*` packages in a signed `[aces]` repo built the `omarchy-pkgs` way: pinned sources, container builds, edge → rc → stable (FP §14) | `omarchy update` (ACES hooks run after it) |
| Images | OCI images by digest, built on DARKNESS and shipped (no PyPI on the rig; FP §11.9) | the server agent loads them |
| Models and LoRAs | content-addressed bundles with a manifest (licence, quantisation, evaluation); gated weights are downloaded by the operator under their own acceptance | manifest pointer |
| ISO | `omarchy-iso-make --local-source` over the pinned Omarchy packages plus the ACES PKGBUILDs | per release |

**Install and layout.** The `cidata` profile lists `aces` as an extra package *(unverified whether Omarchy's
configurator accepts extra packages; if not, a one-line installer patch is carried)*; `aces-firstboot.service` does the
rest. Root-owned defaults live in `/etc/aces`; user overrides in `~/.config/aces`; plugin ids use the `aces.`
namespace; the Python stack ships as one locked venv package.

**Updates and rollback.** A pacman PreTransaction hook takes a snapshot before any transaction *(mechanism to confirm
against Omarchy's system snapshots)*; the post-update hook runs `aces-selftest` (plugins validate, fabric reachable,
policy hash, ledger verify, ArtCraft smoke) and offers one-command rollback. Channels: DARKNESS on **rc**, a VM canary on
**edge**, customers on **stable**. On the server, images and models flip by digest; a solver changes only with a passing
validation record (FP §2); the host is never touched (FP §11.5). Validated solver and model digests stay available for
the whole subscription term, so a year-one study re-runs on the identical image.

**Licence register.** A register and SBOM generated from pacman track every component in the ISO and the bundled
mirror, including copyleft source obligations of a redistributed mirror, the three ArtCraft licence files not yet read,
and the Gemma 4 text-encoder terms *(unread)*. Appendix A is the current table.

**Trademark.** Omarchy's code is MIT; the name's trademark status is unstated (FP §14). ACES OS carries its own name and
branding (Omarchy's `plymouth set`, screensaver and About hooks) and says "built on Omarchy". A trademark search on
"ACES OS" and a question to 37signals about the phrase are open items. The pre-mortem raised a real collision: **"ACES"
is also the Academy Color Encoding System**, the colour standard of the film and VFX industry the creative suite enters
*(assessment)*; the studio and the video lane need a name that does not read as that standard.

### 2.1 Engine first, OS optional

The pre-mortem's verdict was that the wrapper could eat the engine: the engine (simlab, gendesign, the ledger) is built
and needs a demonstrator and a pilot, while the wrapper (ISO, Quickshell plugins, Fusion VM, seven alpha apps, field
writes, hosted lanes) is twenty-plus first-time deliverables for one developer. Rev B therefore adopts two rules:

- **Everything below the desktop runs on any OS.** The fabric daemon, router, broker, CLI, MCP servers, web consoles and
  containers work from a Windows-booted DARKNESS, a plain Linux box or a laptop. ACES OS is the best place to run them,
  not the only place. This also answers the buyer-IT objection that engineers run Windows CAD and IT will not deploy a
  Hyprland desktop: the sellable unit can be **ACES Server plus OS-agnostic clients**, with ACES OS for the engineers
  who want it.
- **All Omarchy coupling lives in one package, `aces-omarchy`** (plugins, hooks, theme, launcher entries, skill
  symlinks). Leaving or re-basing on upstream costs one package.

**The v1 install path is an overlay, not a fork:** a stock Omarchy 4.0.x ISO plus one bootstrap that adds the signed
`[aces]` repo and installs the `aces-os` meta-package. The ACES ISO is built in CI from day one as a canary and as the
appliance image, but it becomes the product only when the name clears and two pilots pass.

### 2.2 The release train and supply chain

- **Nightly canary** on Omarchy edge in a QEMU VM: `omarchy-shell shell ping`, web-app launch, hooks fire, a screenshot,
  the ArtCraft smoke tests. Red for three nights blocks the ACES channel from advancing.
- **Arch Archive date pin** per ACES release, so a rebuild months later sees the same package set.
- **Every AUR recipe ACES depends on is forked, pinned and clean-chroot built** (ArtCraft, openEMS, cantools, snap7,
  can-utils); nothing installs live from the AUR on a customer machine.
- **Offline-first supply chain:** an OCI registry mirror, a wheelhouse index and signed bundles (cosign signatures, SBOM)
  replace `docker save | ssh docker load` and the agent-as-deployer path. The same mechanism serves sovereign and
  air-gapped customers.
- **Support N and N-1** Omarchy versions only.

---

## 3. The compute fabric

### 3.1 Lanes and the manifest

A **lane** is a named capability: `model/<name>` (OpenAI-compatible), `sim/<pool>` (simlab-mcp + queue),
`video/<name>` (LTX worker API), `embed/<name>`, `device/<id>` (section 5b). On the desktop, **`aces-fabricd`** reads
GRID CONTROL `/api/modes` and `/api/status` and rig-state `:8010`, and publishes the **lane manifest**
(`~/.config/aces/fabric.json`): the human-set mode, the lanes with endpoints, health and sovereignty class. It has no
write path to GRID CONTROL. The rig address and a pinned key are set at install; there is no broadcast discovery, and
the rig needs a TLS front *(to build)*.

**`aces-router`** is a localhost OpenAI-compatible endpoint that resolves logical names (`aces/coder`, `aces/flagship`,
`aces/embed`, customer-defined lanes) against the current mode. A missing lane returns "not in mode X"; it never falls
back silently to a cloud model. It applies DeepSeek's `encoding` scripts for the V4 lane (the model ships no Jinja
template; FP §7).

The capacity truth table from Phase 3 stays the law for simulation: a GPU is usable only when rig-state reports it
idle, GRID CONTROL does not flag it derated, the active mode's pool contains it, and no lease holds it (FP §2).

**Capability descriptors, not GPU indexes.** Lanes are keyed by measured capability (VRAM, FP64 rate, FP8 or FP4
support, P2P, thermal rank), so GPU 6 can swap with a healthy simulation card after its soak without any client
changing, and DARKNESS (Blackwell, FP8 capable, 24 GB) and the rig (GA100, no FP8, 64 GB) are simply two CI profiles.
**`aces lane test`** qualifies a model lane before agents may use it: tool-call fidelity through the model's own
encoding, eight-agent concurrency, tokens per second against Aaron's threshold *(target)*. A model swap becomes a
configuration change that must pass the same test.

### 3.2 GPU modes for the first iteration

| Mode | GPUs 0–3 | GPUs 4–7 | Lanes |
|---|---|---|---|
| `aces-work` (new; workday default) | simulation pool (sim-lab-4 rules) | DeepSeek-V4-Flash-0731, pipeline-parallel across the four cards; each card's shard plus KV cache kept under 60 GB (FP §11.4) | `sim`, `coder` |
| `aces-studio` (new) | LTX-2.5 workers ×4 (int8 distilled DiT 21.5 GB + int8 Gemma4-12B encoder per card; FP §6) | model lane as above | `video`, `coder` |
| `sim-lab-8` (exists) | all eight simulate | — | `sim` |
| `video-studio` (exists) | LTX-2.5 ×6 + editor + director | — | `video` |
| `general-flagship` (exists) | GLM-5.2 via ik_llama.cpp on cards 0–5+7 | — | `flagship` |
| `aces-training` (new) | LoRA / QLoRA jobs | model lane kept | `train` |

Modes are GRID CONTROL modes, switched by a human. The `aces.fabric` bar widget shows the mode and opens GRID CONTROL as
a web app; agents can only **file a mode request**. GPU 6 carries a derate flag in GRID CONTROL today (rig notes); Aaron
decided it is in the working set, so the plan is a **24-hour thermal soak before the flag is cleared**, with a thermal
watch on that card in the manifest afterwards.

### 3.3 The model lane

| Candidate | Fits 4 × 64 GB fully resident? | Status |
|---|---|---|
| **DeepSeek-V4-Flash-0731** (≈ 290 B, ≈ 13 B active, MIT; FP §7) | **Yes** (≈ 145–165 GB) with 70–90 GB for KV cache | **Decided.** Serving paths to benchmark: vLLM pipeline-parallel with INT4 weight-only kernels (Marlin/GPTQ repack; GA100 has no FP8/FP4 hardware; conversion *unverified*) and ik_llama.cpp GGUF Q4_K_M. Tensor parallel over PCIe Gen 2 is slow, not impossible; pipeline parallel is what the fleet modes use. |
| GLM-5.2 (≈ 750 B, MIT) | No (≈ 375 GB at 4-bit) | Stays the seven-card flagship mode. KTransformers hybrid on four cards is a benchmark only, never a commitment. |
| DeepSeek-V4.1-Flash (552 B + 196 B Engram) | Marginal | Benchmark only. |

**Benchmarks before anything is promised** *(targets)*: tokens/s at one and eight concurrent agents on both stacks;
DSpark speculative decoding on and off; tool-call accuracy with Codex, OpenCode and Hermes against simlab-mcp; the
GPU 6 soak; FP64 sweep throughput on GPUs 0–3 unchanged while the lane runs.

### 3.4 Without the rig, and when DARKNESS is booted into Windows

ACES OS must be a good engineering desktop with no server: local lanes only (Ollama or vLLM on the 24 GB card), voxel
FEA and SIMP at workstation speed recorded in the ledger as **preview-grade** (every run is evidence, FP §11.3; only
rig runs sign off), build123d and the CAD skills, the ArtCraft apps, cloud agents through Omarchy's agent accounts
where the project class allows. A laptop gets the same OS with GPU pillars greyed out honestly.

Dual boot makes DARKNESS an **intermittent host**. Always-on services that live on it today (Nabu's evidence API,
qdrant, the agent gateways) move into rig containers; the ledger queues locally until it can deliver; the project store
lives on the LAN (rig NVMe) and is mounted by both boots; ACES never writes to the Windows partitions.

---

## 4. Feature pillars

| # | Pillar | What it is | Status | Day one on DARKNESS | v1 (first sellable) |
|---|---|---|---|---|---|
| 1 | **Omarchy base, untouched** | Omarchy 4 from a pinned mirror snapshot; no Omarchy file edited | wire | install | pin per release |
| 2 | **Packaging, ISO, updates** | signed `[aces]` repo with channels, ISO, snapshots, selftest, rollback, licence register | build | local repo | signed stable channel |
| 3 | **Compute fabric** | `aces-fabricd`, lane manifest, `aces-router` with logical names | build | manifest by hand | daemon and router |
| 4 | **Policy broker** | one MCP gateway with read / submit / consequential tiers and physical-presence approvals | build (v0 in days 1–30) | read + submit tiers | consequential tier with `aces.approvals` |
| 5 | **Evidence ledger** | simlab's hash chain extended to tool calls, approvals, device transcripts, egress, updates; open format and verifier | wire | simlab ledger | extended records, post-boot verify |
| 6 | **Simulation and generative engine** | queue, leases, validated registry, SIMP, parametric loops, surrogates, FP64 default | exists | client to the rig | console on the rig; registry growth per the runtime plan |
| 7 | **Voxel kernel ("ACES Voxel"), standard** | exact voxelisation, implicit modelling, lattices, topology fields, remeshing; **in every install** | wire | runs natively | one `aces-voxel` package and CLI |
| 8 | **CAD adapters** | Fusion API lane through the Windows VM (bridge :9876), Onshape (official FeatureScript MCP + REST), build123d | build | Fusion via Windows boot; Onshape in the browser | VM lane behind the broker; Onshape pull adapter |
| 9 | **Model lanes** | DeepSeek-V4-Flash-0731 on GPUs 4–7; GLM-5.2 flagship; bring-your-own endpoints | build | existing lanes | `aces-work` mode benchmarked |
| 10 | **Creative suite** | seven ArtCraft apps, pinned, status-labelled, behind the broker | build (days 0–30) | installed from pinned packages | rebuilt from pinned tags or commits with a CI smoke gate |
| 11 | **ACES Video Studio** | ComfyUI + `ltx-pipelines` in the video modes, a content-addressed LoRA registry, `ltx-trainer` as a leased SIMLAB job | build | web app to the rig's video-studio mode | registry and trainer jobs |
| 12 | **Field and device integration** | device registry, `aces-devd`, bus/PLC/robot/RF/analysis packages, approvals and transcripts (section 5b) | build | packages installed; read-only tools | registry, consequential tier, transcripts |
| 13 | **Shell surfaces** | `aces.*` plugins, web apps, hooks, Parameter Sky theme | build | web apps | plugins |
| 14 | **Manufacturing outputs** | robot `.JBI` (formgen), G-code, DXF, STEP, SendCutSend and Bambu paths | exists (skills) | skills | `aces make` routing |
| 15 | **Dual-boot DARKNESS** | Windows ("what is") beside ACES OS ("what will be"), one fabric, LAN project store | build | install | always-on services on the rig |
| 16 | **Commercial and compliance pack** | lease, registry subscription, Programs, lawyer-built compliance pack | later | — | before the first external install |

---

## 5. The engineering loop, end to end

1. **Open a project.** `aces new <name> --class sovereign` writes `aces.yaml` (company, R&D activity, hypothesis, class)
   in `~/Work/<name>` on the LAN project store. The class drives routing, egress and approvals from here on.
2. **Pull geometry.** `aces cad pull fusion://<doc>` (through the VM bridge, behind the broker) or
   `onshape://<doc>/<ws>/<el>` (REST export) → STEP + parameter snapshot + sha256. Sovereign projects use build123d and
   the voxel kernel; Fusion documents live in Autodesk's cloud *(assessment)* and Onshape is a cloud service, so neither
   is allowed for `sovereign` or `controlled` classes.
3. **Voxelise or model implicitly.** `aces voxel from-step part.step --res 0.5mm` (exact `mesh.contains`
   voxelisation) or an implicit script (SDF primitives, formgen lattices) → a field the solvers understand.
4. **Simulate.** `simlab submit exp.yaml` → the rig's queue, one case per GPU, FP64 by default, validation record
   attached, MLflow run, evidence.json. Previews on DARKNESS are recorded as preview-grade.
5. **Generate.** Sweep → surrogate (registered with its error) → optimiser (NSGA-II / Optuna / Warp gradients) →
   **verify the winner on the real solver**.
6. **Return to CAD.** Results go back as STL/3MF or STEP (where analytic) through the Fusion bridge or the Onshape API,
   as a guide to rebuild parametrically.
7. **Make.** `aces make jbi|gcode|dxf|step`; SendCutSend or Bambu upload where wanted.
8. **Bench.** Section 5b: push a program to the test controller, capture the vehicle bus, log the instrument, each as a
   broker-mediated session with a transcript.
9. **Show.** `aces studio render` turns the design (turntable, exploded view, field animation) into an LTX clip on the
   server with the Refine-Details or Pixel-Spatial-Upscaler adapters.
10. **Prove.** `aces evidence build` bundles spec, hashes, validation records, provenance, device transcripts and
    observations into the ledger; `simlab report` writes the document; the record reaches Nabu on the rig.

Skills that exist today: `cad`, `dxf`, `gcode`, `bambu-labs`, `sendcutsend`, `step-parts`, `implicit-cad`, `cad-viewer`,
`cad-electronics`, `fusion-cad-agent`, `engineering-viz`, the simlab agent brief. To write: `aces-engineering` (the loop
as a procedure with the MCP tools), `aces-cad-onshape`, `aces-voxel`, `aces-studio`, `aces-evidence`, `aces-field`.

**Mechanisms the engineer's draft added (Rev B):**

- **One `CadSource` contract.** Fusion, Onshape and build123d all implement the same pull (STEP + parameters +
  sha256, extending `simlab export`) and the same push (a *proposed* version that a human accepts in the CAD tool).
  Agents and the loop never see the back end. The Fusion API reference already harvested (17,993 records, FP §2) is
  indexed behind `rag_search` so agents write correct bridge code.
- **Preflight lint.** A skill checks every `experiment.yaml` against the runtime's numerical rules before submission
  (FP64, consistent tractions, `converged` flags, `vram_gb` under 60; FP §2), so the rules live in code, not in memory.
- **Independent second discretisation for sign-off.** A voxel winner that is stress-critical is cross-checked on a
  conforming-mesh solver (CalculiX or Code_Aster on CPU, MFEM+cuDSS when built) before it is called verified; the
  voxel resolution is recorded in every validation record. This answers the pre-mortem's point that the signing-off
  solver is itself voxel FEA (validated on beams and one bracket so far).
- **Measurements are evidence.** CAN logs, instrument traces, robot run logs and SDR captures (stored as SigMF) join
  the ledger beside solver runs; a model-versus-measurement residual becomes a validation record. This is how the
  platform closes its own "no hardware validation" gap (FP §2) without a hot-fire.
- **The digital thread.** Every G-code, JBI, DXF, STEP and render carries a sidecar with its ledger hash; "explain this
  file" walks back to inputs, solver digest, approvals and operator. The offline verifier is free.
- **`diagnose-case`.** Modelled on Omarchy's `diagnose-crash`: a failed case hands its `evidence.json` and logs to the
  default agent.
- **Nightly sweeps** run through `omarchy agent prompt` on the submit tier only, watched in Herdr.

## 5b. Field and device integration

The loop does not end at a file. Engineers commission PLCs, drive robot cells, instrument vehicles, log into
embedded devices, pull apart firmware they need to interoperate with, and measure radios. ACES OS treats all of this
as **native engineering access to equipment the operator owns or is authorised to work on**, from the same desktop,
the same agent layer and the same evidence ledger. Almost everything needed is already an Arch package (FP §15).

| Domain | What ships (`aces-field`) | v1 | Notes |
|---|---|---|---|
| **PLC and industrial** | python-snap7 (Siemens S7), pymodbus, asyncua (OPC UA), pycomm3 (Allen-Bradley), pyvisa (bench instruments); `aces plc` CLI; MCP tools `plc_tags`, `plc_read`, `plc_write` (approval-gated) | tag browser panel; writes approved and logged per project and operator | Vendor engineering suites stay on the Windows VM or CAD node |
| **Robotics** | ROS 2 **as a container, not an AUR rebuild** (320 `ros2-*` AUR packages would swamp one maintainer); `urdf/srdf/sdf` skills; formgen's `.JBI` emitter for the Yaskawa HC10DTP; the cell's ZED X and Jetson tooling | `aces robot` sends a job over the controller's documented interface and records the program hash; bag capture into the ledger | Motion is a consequential action: approval, e-stop confirmation, interlocks respected |
| **Automotive and CAN** | SocketCAN with python-can, can-utils, cantools (DBC), udsoncan (UDS), USB-CAN adapters | `aces can capture|decode|replay` with DBC libraries per project; UDS sessions logged; HERMES and AERIS-X64 are first users | Transmit and diagnostic writes need approval; vehicle safety stays the operator's responsibility |
| **Device access over SSH** | OpenSSH, paramiko, Ansible, pyserial; a device inventory with per-device keys and session recording | playbooks for lab devices run from the desktop with transcripts in the ledger | Keys in the OS keyring; agents get read-only device tools unless a human grants a write session |
| **Firmware and binary analysis** | Ghidra (Apache-2.0), rizin, binwalk (MIT), qemu, all in Arch `extra` (the Arch package named `cutter` is unrelated; the rizin GUI needs its own packaging) | runs in a network-less container on images from the project's own equipment; findings go into the project record | Use is bound by the project's authorisation record |
| **RF analysis and radio modules** | GNU Radio, SoapySDR, rtl-sdr, HackRF, Ettus UHD, gqrx, SigDigger, Universal Radio Hacker (all Arch `extra`); openEMS (AUR, installable, not yet validated), Palace and gprMax (planned) for simulation | `aces rf capture|spectrum|decode|simulate`; **receive and simulation only in v1**; transmit stays disabled until a spectrum licence record (ACMA) is attached to the project | Controlled items (the AERIS-X64 radar work) carry the Defence Trade Controls gate; RF simulators are not evidence-grade until they carry a validation record (FP §11.2) |
| **Survey and photogrammetry** | QGIS + FlyPath (DJI WPML KMZ missions for DJI Fly; the Mini 5 Pro and the other consumer DJI aircraft have no SDK, FP §16), WebODM with the ODX engine on the desktop GPU under Docker, CloudCompare (AUR) for point clouds; `aces-webodm` and `aces-survey-setup` (`aces-survey`) | plan in QGIS, fly the KMZ from DJI Fly, process in WebODM, deliver from QGIS and CloudCompare; GCPs for survey grade (no RTK on the Mini series) | Imagery of other people's property and of controlled sites follows the project class and the end-use gate; AGPL and GPL components run as containers and user-profile plugins, never linked into aces-* code |

**Mechanism.** A device registry in `/etc/aces/devices.d` records owner, authorisation basis, interfaces (SSH, serial,
SocketCAN, S7, Modbus, OPC UA, VISA, SDR receive), permitted operations, safety class and project class. **`aces-devd`**
is the only process that holds the interfaces (udev groups, nftables uid rules); humans and agents reach it only
through the broker. Sessions start read-only. **Twin first:** the physical bus lives in a broker-only network namespace
and agents see a virtual twin (`vcan` for CAN, simulators for PLC and robot targets) by default; a real session is a
separate, approved step. Approval is a fingerprint or FIDO presence check through polkit, never an on-screen button an
agent could click; field agents run sandboxed or as their own user, so `omarchy plugin add --yes` and package installs
are out of their reach. Writes, motion, flashing and configuration pushes are consequential
actions, each with its own re-authenticated approval, and anything that moves also needs on-site confirmation. Every
session produces a ledger-chained transcript (commands, frames, file hashes). `controlled` projects block cloud models
and egress and require compliance sign-off. The compliance pack (built with an AU lawyer) covers the Defence Trade
Controls Act, ACMA spectrum licensing, and machine and vehicle safety. The field pack is sold only after customer
screening; it is not part of a free public image.

---

## 6. The creative suite as OS citizens

**ArtCraft (seven apps).** PhotoCraft, VectorCraft, FilmCraft, LightCraft, PdfCraft (PrintCraft's current name),
EffectCraft and DesignCraft ship on day one as `aces-artcraft-*` packages. The AUR PKGBUILDs are the starting point;
packages are rebuilt from **pinned upstream tags, or pinned commits where no tag exists yet** (PdfCraft is only on the
AUR as `pdfcraft-git`; FP §5), with `pdfcraft` providing and replacing `printcraft`. Status labels are data refreshed
each release (PhotoCraft and PdfCraft early alpha, the other five in development) and shown in launcher entries and an
About panel. Each app's CLI, JSON channel or MCP server sits **behind the broker**; writes stay in the project
directory; outputs are hashed to the ledger. A CI smoke test per app (offscreen launch, one control command, a
screenshot) gates promotion to rc and stable. Theme sync happens through the theme-set hook only if the apps accept a
palette *(unverified)*. The apps sit outside the SLA until upstream reaches beta. The separate ArtCraft studio (cloud
models) is excluded.

**ACES Video Studio.** Lightricks' SaaS is not shippable and its name is not used. ACES ships its own studio: ComfyUI
with the LTX-2.5 nodes plus `ltx-pipelines` in the human-switched video modes, opened as a web app; the int8 or bf16
checkpoints on GA100 (NVFP4 needs Blackwell; FP §6); the dev transformer for training; DFR for production renders.
A **content-addressed LoRA registry** on rig storage records each adapter's base (2.5 or 2.3), licence, source hash,
training-data hash, `ltx-trainer` configuration and evaluation clips, for the first-party family (Pixel-Spatial-
Upscaler, Refine-Details, SDR-To-HDR, Restore, Colorization, Deblur, Decompression, Clean-Plate, Alpha-Gen,
Layout-To-Render, Water-Simulation, Ingredients, Slow-Motion-Control, Cinemagraph) and every adapter Aaron trains.
`ltx-trainer` runs as a SIMLAB job, so it is leased and evidence-recorded. The "director" and "editor" roles in the
rig's video-studio mode become ACES skills.

**Licence handling.** LTX-2.x Community License: free commercial use under US$10 M annual revenue measured across the
whole entity and its affiliates; a paid agreement above; fine-tune transfer may need a licence (FP §6). ACES never
redistributes LTX weights or adapters; the operator downloads under their own acceptance and attests revenue; entities
over the threshold are routed to Lightricks before the video lane is enabled; the Gemma 4 encoder terms are read before
the studio ships externally; the AU lawyer reads LICENSE-2_x first.

---

## 7. Model policy: "we run the servers" and "any model they want"

- **Every current project is class `personal`** (decision 7): a hobby with no contract, customer or defence
  engagement, with no component restricted and every lane available. A project is reclassified only at the end-use
  gate, when it gains a customer, a contract, a paid deliverable or a defence end use.
- **Project classes drive routing once assigned.** `sovereign` and `controlled` projects never see a cloud model;
  Omarchy's cloud agent launchers are hidden for them. `open` and `commercial` projects may burst to Claude Code, Codex and the rest
  through Omarchy's agent accounts and usage panel, with a consent record per project and zero-retention terms where
  the vendor offers them.
- **Bring your own model.** Any OpenAI-compatible endpoint, Ollama on the desktop, or a customer's own server is declared
  as a lane with a sovereignty class; the router validates it (tool-calling, context length, throughput) before agents
  may use it.
- **ACES-served lanes.** DeepSeek-V4-Flash-0731, the GLM-5.2 flagship mode, GLM-4.7-Flash and the embedding lane on the
  customer's server, with pinned versions, evaluations and change notes. **Hosted lanes we operate are a later phase**
  (Aaron's words stand: the plan defers hosting rather than redefining it).
- **Egress.** nftables denies outbound traffic by default for `aces-*` service uids with per-class allowlists; device
  subnets are reachable only from the `aces-devd` uid.
- **Approvals.** Agents run as the same Unix user as the shell and third-party plugins are unsandboxed (FP §14), so the
  broker is the security boundary and consequential approvals need a **physical-presence factor** (fingerprint or FIDO
  through polkit `auth_self`); one approval covers one action and expires; agents never hold upstream tokens; the policy
  file is root-owned and its hash is checked at boot.

---

## 8. Product and go-to-market

### 8.1 SKUs

| SKU | Contents | Form | When |
|---|---|---|---|
| **Programs** | one Noyron-pattern study: specification, sweep, surrogate, verified winner, evidence bundle, report stating verification scope | fixed fee per study | **first revenue**; sensitive firms pilot on a leased Server instead of Aaron's rig |
| **Community** | ISO + `[aces]` repo: Omarchy base, voxel kernel, SIMLAB client, CAD adapters, the seven ArtCraft apps; bring your own GPU and models; no field pack | free; ACES code open | after the licence review closes |
| **Workstation** | + signed stable channel, validated solver images kept for the term, support, compliance pack, field pack after screening | per-seat subscription | design partners first |
| **Desktop + Server** | + ACES Server image (GRID CONTROL, SIMLAB, model / sim / video lanes) on **stock hardware** we maintain | 3-year lease plus subscription (the Omniscient machinery, FP §10) | after the hardware BOM |
| **Hosted lanes** | our servers for customers without hardware | metered GPU-hours | later, after a security review |

**The sellable unit.** Buyers' engineers run Windows CAD and their IT may refuse a Hyprland desktop, open weights of
PRC origin or an OS whose founding patron is Alibaba Cloud *(pre-mortem assessment; test it in the first buyer
conversations)*. Because everything below the desktop runs on any OS (section 2.1), the unit that is sold can be
**ACES Server plus OS-agnostic clients** (CLI, MCP, web consoles on the engineer's existing machine), with ACES OS as
the full experience for the engineers and firms that want it. The plan does not depend on every seat running Omarchy.

**Server hardware.** The reference rig's unlocker-patched CMP 170HX cards are a development asset, not a bill of
materials: the driver is frozen, the cards are unsupported as a product, and NVIDIA's terms for their commercial use are
unread *(unverified)*. The Server SKU names stock GPUs re-measured for FP64 and lane fit before any quote; **FP64 per
dollar** is the procurement metric. Whether the rig may run paid Programs is a legal question (Appendix C).

### 8.2 Open and paid

- **Open (recommended MIT):** shell plugins, web-app launchers, CAD adapter libraries, the `experiment.yaml` schema,
  the evidence format and its verifier, the fabric daemon and router. The voxel kernel is standard in every install; its
  source licence is decided before the first external install (an MIT release cannot be withdrawn).
- **Paid:** the signed channel, digest-pinned validated solver images, support, the compliance pack, the server image,
  benchmarked lane configurations, Programs.
- **Open-sourcing the evidence format and verifier** lets R&DTI advisers and auditors check a bundle without buying,
  which makes advisers a channel.
- **Two licence policies.** The audited rules (section 13) govern distribution. The operator's own installation runs
  in `trial` mode with every catalog row installable and overrides recorded in the ledger (decision 6); what is shipped
  to a customer is filtered by the distribution policy at build time, so the two never mix.

### 8.3 Pricing inputs (no numbers yet)

Stock Server BOM and power; support hours per seat measured on customer zero; upstream churn cost; GPU-hours per study;
lease term and residual; the buyer's current CAD and simulation spend (Fusion Simulation Extension A$2,636–3,101/yr,
FP §8); the customer's own R&DTI position (eligibility is their adviser's call; the 43.5 % refundable rate applies only
under A$20 M aggregated turnover); LTX pass-through above US$10 M; customer-supplied Fusion and Onshape licences.

### 8.4 Positioning, honestly

| Alternative | Fact | ACES line |
|---|---|---|
| Noyron (LEAP 71) | proprietary; sells designs and engines, not software (FP §10) | the same loop on owned hardware; no parity claim until a hardware-validated design exists |
| nTopology | incumbent implicit-modelling tool (FP §10) | owned FP64, open solvers, evidence; ACES's implicit UI is immature |
| Fusion simulation and generative design | cloud-only, no API, A$2,636–3,101/yr (FP §8) | Fusion stays the CAD of record for `commercial` projects; local solves with an API; benchmark Fusion GD on the bracket first |
| Onshape | browser-native, official FeatureScript MCP (FP §8) | the Linux-native CAD path for `open` and `commercial` projects; adapter unbuilt |
| Cloud AI agents | capable, but geometry leaves the building | project classes decide; sovereign work never bursts |

### 8.5 Launch sequence

Customer zero → private preview ISO → paid design-partner Programs → Workstation seats → Server lease (BOM and
compliance sign-off) → public Community ISO (licence review) → hosted lanes (security review).

### 8.6 Proof points before any sale

- A Noyron-pattern demonstrator on unrestricted geometry with `simlab evidence verify` passing. **PyFR's only
  validation record is the Re-200 cylinder (FP §2), so an aerospike contour sweep needs its own validation case before
  its results count as evidence.**
- Measured DeepSeek lane benchmarks (section 3.3), published either way.
- A timed unattended `cidata` install on a second machine with the seven ArtCraft apps launching and `aces-selftest`
  passing; a rollback drill.
- The Fusion GD 14-day trial benchmark against ACES on the bracket.
- An approved, transcribed device session in the ledger.
- An independent R&DTI adviser's written view of a bundle; an AU lawyer's read of LTX, ArtCraft, the Omarchy name and
  the field-tool compliance notes.

---

## 9. Day one on DARKNESS

DARKNESS **dual-boots** (decided 2026-10-08: "swap between what is and what will be"). Fusion, LTX Desktop and Nabu keep
their Windows boot; ACES OS is installed beside them with Omarchy's free-space install; the Windows VM path is proven
on the ACES side before any cut-over.

1. **Pre-flight (Windows):** full backup; record the BitLocker recovery key; export the Fusion add-in and MCP configs;
   note the LTX Desktop hold; put `cudax-sim-runtime` under git; build the current images and ship them to the rig.
2. **Install Omarchy 4.0.4** dual-boot; choose Claude Code as the default agent; theme and branding. Check: both boots
   start and Fusion still runs in Windows. Open question: whether the nvidia-open DKMS module for the RTX PRO 4000
   builds against `linux-omarchy`.
3. **`aces-base`:** fabric manifest (by hand at first) pointing at the rig; `.mcp.json` (simlab + rig-state with a
   submit-tier token); skills symlinked; hooks (post-boot fabric check, theme-set for the consoles); broker v0 (read and
   submit tiers, ledger records).
4. **`aces-engineering` and `aces-voxel`:** simlab client, gendesign, build123d, the CAD skills; run the bracket
   validation case from DARKNESS against the rig; verify the evidence record lands in the ledger.
5. **Consoles as web apps:** GRID CONTROL, SIMLAB, MLflow.
6. **`aces-creative`:** the seven ArtCraft packages from pinned sources; check that all seven launch with status labels.
7. **Model lane:** the server agent defines `aces-work`; GPU 6 soaks for 24 hours before its flag is cleared; DeepSeek
   is benchmarked on both stacks; the default agent is pointed at `aces/coder` for a local-only session.
8. **Fusion:** Install > Windows (Docker/KVM VM), install Fusion and the community add-in, forward 9876 behind the
   broker, run `simlab export` against it. Interactive modelling stays on the Windows boot.
9. **Onshape:** web app + the official FeatureScript MCP Server; first `aces cad pull onshape://` through the REST API.
10. **ACES Video Studio v0:** web app to the rig's video-studio mode; render one clip; list the LoRA library.
11. **Field bench:** install the `aces-field` packages; confirm the SDR bench (receive) and a CAN capture on the test rig;
    register read-only `plc_*`, `can_*`, `rf_*` MCP tools; writes stay off until the consequential tier is wired.
12. **Always-on services:** move Nabu's evidence API, qdrant and the gateways into rig containers; point the ledger's
    delivery at them; mount the LAN project store from both boots.

Deferred from day one: native Quickshell plugins, the ISO, Onshape write-back, LoRA training on the rig, hosted lanes.

---

## 10. Risks and what to cut from v1

| Risk | Severity | Mitigation |
|---|---|---|
| Omarchy churn: the shell tracks `quickshell-git`, the plugin schema is v1, releases are fast, one benevolent dictator | High | Pin a mirror snapshot per ACES release; never fork; CI validates `aces.*` plugins against edge before advancing; keep every ACES surface runnable in a browser |
| Agents bypass the broker (auto-approve modes, the Omarchy skill, unsandboxed plugins, shell IPC) | High | Endpoints reachable only through broker sockets and uid rules; root-owned policy with a boot-time hash check; physical-presence approvals; submit-tier tokens |
| Fusion does not run on Linux; the official MCP needs a commercial subscription (lapsed) | Medium | Dual boot for modelling; the VM lane for API export; Onshape and build123d natively; a Windows CAD node for teams; which Fusion licence covers the VM lane is an open item |
| ArtCraft apps are alpha and fast-moving | Medium | Pinned tags or commits; CI smoke gate; status labels; outside the SLA; PSD/PDF/EXR interchange as the fallback |
| LTX licence threshold and redistribution; Gemma 4 encoder terms unread | High | No weights in any image; operator-side acceptance and revenue attestation; lawyer's read before the first external studio customer; settle which entity sells ACES (affiliate revenue counts) |
| The Server SKU rests on an unreproducible rig | High | Quote stock hardware re-measured for FP64; the rig stays the reference; legal read of NVIDIA terms before paid Programs on it |
| DeepSeek lane slow after the INT4 repack over PCIe Gen 2, or GPU 6 overheats | Medium | Benchmark both stacks first; single-card lanes as fallback; restore the derate flag on a thermal fault |
| Dual-boot damage (boot entries, BitLocker, hibernated NTFS) | Medium | No Linux writes to Windows partitions; recovery key held; project store on the LAN |
| One operator, no support organisation, no external customer | High | Programs before seats; measure support hours on customer zero; sell SLA only to capacity; runbooks and digest-addressed images so the server agent is not a bottleneck |
| Claims outrun evidence (Noyron parity, R&DTI eligibility, design fitness) | High | Proof-point gate; benchmarks published either way; reports state verification scope; engineer of record signs off; lawyer drafts liability terms |
| Dual-use field tools used beyond authorised scope | High | Device registry, read-only default, per-action approvals, transcripts, transmit off, customer screening, the compliance pack |
| Trademark use of "Omarchy"; "ACES OS" unsearched; "ACES" collides with the Academy Color Encoding System in the film and VFX space the suite enters | Medium | Own name and branding; clearance search before any branding spend; a different name for the studio and video lane; ask 37signals about "built on Omarchy" |
| **The wrapper eats the engine:** twenty-plus first-time wrapper deliverables for one developer while the built engine waits for a demonstrator and a pilot | High | Engine first, OS optional (section 2.1); the golden path is the only gate; freeze additions at day 30 if it is red; v1 scoped as "ACES 0.1 Bench" (section 11) |
| Buyer IT rejects Hyprland desktops, PRC-origin open weights or the Alibaba patronage; the OS is never dogfooded because Aaron stays in Windows | High | Sell Server plus OS-agnostic clients where needed; log ACES-OS days per week with a post-boot hook as a tripwire (under three days a week at day 30 means the cut-over plan is wrong) |

**Cut from v1:** hosted lanes and any multi-tenant service; the public Community ISO until the licence review closes;
the ArtCraft studio; distributing any trained LoRA or bundled weights; SLA cover for creative apps; RF transmit and any
unattended device write; DeepSeek-V4.1-Flash and KTransformers GLM-5.2 on four cards (benchmarks only); the GLM-5 CAD
fine-tune; interactive Fusion in the VM; VFIO passthrough; multi-user RBAC; a curated model catalogue (bring-your-own
endpoints suffice); native Qt Quick viewer; multi-node fabric; Onshape write-back.

---

## 11. The first 90 days *(targets)*

### 11.1 Scope of v1: "ACES 0.1 Bench"

The pre-mortem's minimum v1, adopted with Aaron's decisions layered on: signed packages on stock Omarchy, the engine
usable from either boot of DARKNESS, and the **golden path as the only acceptance gate**: STEP (build123d, Onshape,
or a Fusion file export) → voxel → sweep → surrogate → verify → evidence → report, agent-driven on the DeepSeek lane
once `aces lane test` passes. Five things must be green before anything else merges: the project workspace, the
fabric, the golden path, the evidence chain, and passive field access (capture, decode, read, receive, with simulator
twins; no write path exists in v1). The seven ArtCraft apps are installed on day one as their own package group with
no dependents. The video studio runs on the rig with weights fetched by the licensee. Support is pilot-only with an
`aces doctor` bundle and no remote shell.

### 11.2 Gates and tripwires *(targets)*

| Gate | Condition | If red |
|---|---|---|
| Day 14 | `cudax-sim-runtime` and `aces-os` under git with CI; release-only rig deploys through the signed supply chain; GPU 6 soak complete; `aces lane test` harness runs; trademark clearance search done; plan to relocate always-on services agreed; the Cowork ACES scope reconciled with this document | Fix before adding anything |
| Day 30 | Golden path green from the ACES boot; seven apps launch; benchmark table recorded | **Freeze additions** until green |
| Day 45 | A second engineer runs the golden path from the docs on a fresh install | Rewrite the docs, not the code |
| Day 90 | Pilot go/no-go on repeatability, ACES-OS hours per week, and the buyer-IT answer | Re-plan the sellable unit |

Tripwires logged automatically: the nightly edge canary red for three nights; any ArtCraft crash-on-open in release
smoke; fewer than three ACES-OS days a week at day 30 (post-boot hook); GPU 6 throttling during the soak; any agent
write outside a twin.

### 11.3 Deliverables

| By | Deliverable | Owner | Acceptance |
|---|---|---|---|
| **Day 30** | Signed `aces-pkgs` repo (edge/rc/stable) and keyring; `aces-base` (fabricd, router, broker v0 with read and submit tiers, ledger records), `aces-engineering`, `aces-voxel`, the seven `aces-artcraft-*` packages, `aces-consoles`; ISO via `--local-source`; `cudax-sim-runtime` under git | Claude Code (workstation) | An unattended `cidata` install in a libvirt VM passes `aces-selftest` and `omarchy plugin validate`; seven apps launch with status labels |
| | DARKNESS backed up and dual-booted; GPU 6 soaked; `aces-work` mode approved | Aaron | Both boots start; Fusion still runs in Windows |
| | `aces-work` defined; `:8020` ownership confirmed; SIMLAB console and GRID CONTROL rev B deployed; DeepSeek weights staged and served on both stacks | Server agent | Benchmark table with raw logs in `BENCHMARKS.md` |
| **Day 60** | `aces-router`; the Windows VM with `:9876` behind the broker; project classes and egress rules; read-only device sessions; Onshape FeatureScript pull adapter; licence register from pacman; `gen_*` MCP tools | Claude Code | From the ACES boot: Fusion export → STEP → rig solve → ledger entry; a deliberately bad update rolls back; a blocked write appears in the ledger |
| | AU lawyer briefed (LTX, DTC/ACMA, GPL sources, device policy, design liability, selling entity, trademark) | Aaron | Written questions sent; first answers received |
| | PyFR validation case for nozzle flow, then the aerospike sweep, surrogate and verification | Server agent | Validation record in the registry before the sweep is called evidence |
| **Day 90** | ACES Video Studio v0 and the LoRA registry; `ltx-trainer` as a SIMLAB job; the Noyron-pattern demonstrator bundle; a Program report template stating verification scope; preview ISO | Claude Code + server agent | Bundle passes `simlab evidence verify`; preview ISO installs on a clean second machine and passes selftest |
| | Fusion GD trial benchmark; Server hardware shortlist; design-partner approaches; adviser's view of a bundle | Aaron | Written legal and adviser reads received; first Program scoped with consent terms |

---

## 12. Decisions

### Decided by Aaron, 2026-10-08

| # | Decision | Consequence in this plan |
|---|---|---|
| 1 | **DeepSeek-V4-Flash-0731 is the four-card model lane.** GLM-5.2 stays the seven-card flagship mode. | Benchmarks in the day-30 acceptance; the default agent on DARKNESS points at `aces/coder` in Sovereign mode |
| 2 | **DARKNESS dual-boots**, "so I can swap between what is and what will be." | Free-space install beside Windows; Fusion keeps its native boot; always-on services move to the rig |
| 3 | **Ship the ArtCraft apps on day one.** | Pinned packages in `aces-creative` in days 0–30; status labels shown; CI smoke gate for rc and stable |
| 4 | **The voxel kernel is standard in every install.** | `aces-voxel` ships with `aces-engineering`; the open-core line is drawn around operations, not the kernel; source licence decided before the first external install |
| 5 | **GPU 6 is included in the working set.** | Model lane = GPUs 4–7; simulation pool = GPUs 0–3; 24-hour soak before the derate flag is cleared; thermal watch afterwards |

### Decided by Aaron, 2026-10-09

| # | Decision | Consequence in this plan |
|---|---|---|
| 6 | **The operator can override the licence-audit rules on their own installation.** The first iteration is a trial: every catalog row, including the 24 the auditor excluded and the 57 it limited to separate processes, is installable, and Aaron removes items as testing matures. **FluidX3D and OpenSees are included in the first iteration.** | The licence policy splits into a **distribution policy** (the audited rules, enforced for anything ACES ships to a customer) and an **operator policy** (advisory labels on Aaron's own machines). `aces policy mode trial` makes every row installable; `aces policy override <component> --reason` records the operator, the component's terms and the reason in the ledger. The distribution policy applies only to a future shipped product; nothing is distributed today. |
| 7 | **No restriction until an end use is clear.** Every current project is a hobby with no contract, customer or defence engagement, so no component is held back from any project. | Default project class `personal`; the **end-use gate** (section 13) re-reads a component's terms only when a project gains a customer, a contract, a paid deliverable or a defence end use; the ledger supplies the list of components each run used. Labels on components are information, not gates. |
| 8 | **Parts are computed, not drawn.** ACES exists to replace LEAP 71's Noyron-style computational engineering. Anything a generator produces is organic, biologically inspired and futuristic: SIMP topology optimisation with a smoothed iso-surface skin, TPMS and gyroid lattices, implicit blends. A box or a bracket is an input domain, never an output. Every solver and device that can run, runs, and the report says which did (CPU, GPU, Warp, rig lanes) and at what grade. | The golden path's demo is a generative design, its winner ships as a smooth organic STL with renders, and its report carries a compute-used table. Decided 2026-10-09 (afternoon). |

### Still open

7. **Name and branding** for the OS and the studio; trademark search on "ACES OS"; the "built on Omarchy" phrase.
7. **Which entity sells ACES** (LTX counts revenue across affiliates under common control).
8. **Server SKU hardware:** which stock GPUs; whether the rig may run paid Programs (NVIDIA terms).
9. **Is GRID CONTROL part of the customer Server, or Aaron-only?**
10. **Field tools in the sold product:** full `aces-field` after screening, or a signed authorisation profile per customer?
11. **Which AU R&DTI adviser and lawyer** review the bundle format and the compliance pack.
12. **Always-on host and project store** once DARKNESS dual-boots (the plan says rig containers and rig NVMe).
13. **Does Omarchy's stable channel pin Arch core and extra, or only its own packages, and is `quickshell-git` pinned?**
    (FP §4 says it tracks git; FP §14 says every git source in `omarchy-pkgs` is pinned. The Arch Archive date pin in
    section 2.2 covers the gap either way.)
14. **Does the LTX Community License allow ACES to host video lanes for third parties, and is delivering an
    ACES-trained adapter a "transfer"?** (lawyer, LICENSE-2_x)
15. **Artifact transport from DARKNESS to the rig while SSH is denied:** HTTP through simlab-mcp, the OCI mirror, or a
    new channel; and which process serves `:8020` today.
16. **What test-rig hardware exists** (CAN adapters, PLC vendor, SDR), so driver priority is not guesswork.
17. **Does an RFC 3161 timestamp strengthen the ledger as contemporaneous evidence?** (adviser)
18. **Can an int8 LTX worker lease one simulation-pool card** for a single render, or must renders wait for a video
    mode? (a per-clip mode switch is too heavy; the lease rule would cover it if the worker declares `vram_gb`)

---

## 13. Enhancements and open-source integrations to consider

A separate verified catalog, `research/INTEGRATIONS.md` (generated by `research/build_catalog.py`, machine-readable
in `research/integrations.json`), lists about 300 candidate projects across fifteen domains. Each row was checked
against the GitHub API, PyPI, the Arch repositories or the AUR on 2026-10-08 and carries its licence, activity,
packaging and a placement (day-one, v1, later, exclude). The catalog is the considered set, not a commitment. What it
changes in this plan, by domain:

| Domain | What the sweep changes in the plan |
|---|---|
| **CAD kernels and bridges** | Use `altendky/onshape-mcp` (Apache-2.0) or an ACES REST client, not the unlicensed `hedless` server. Split Python environments with `uv` pins: build123d 0.13 pins OCP 8.0, CadQuery 2.8 pins OCP < 8.0, and Arch rolls Python. **FreeCAD plus `freecad-mcp`** gives a native Linux modeller, drawings and CAM that shrinks the Fusion-VM dependency; Autodesk APS cloud export covers Fusion outside sovereign work. Score the model lane on CADGenBench. |
| **Voxel, implicit and meshing (ACES Voxel)** | The concrete stack: OpenVDB and NanoVDB sparse SDFs with a Warp/fVDB GPU path; libigl winding numbers replace `mesh.contains` on leaky geometry; Manifold fuses lattices; Mmg turns densities and SDFs into body-fitted meshes; lib3mf exports beam and volumetric 3MF. Pin the `aces-voxel` venv to Python 3.12 (libigl and pytetwild wheels stop at cp312). GPL tools (CGAL, PyMeshLab, microgen) run as separate processes; TetGen, Triangle, MeshLib and diso are excluded by licence. Add `voxel_*` tools to simlab-mcp. |
| **Solvers and GPU numerics** | Plan solvers verified (NekRS BSD-3, Palace Apache-2.0, dolfinx LGPL-3.0, Chrono BSD-3, Newton and MuJoCo-Warp Apache-2.0, PyFR BSD-3, gprMax GPL-3.0, LAMMPS GPL-2.0). Add a CPU-core lease class for the 192 EPYC threads; build rig images from pinned CUDA ≤ 13.3 bases, never Arch's cuda 13.4; cap JAX and CuPy allocators to `vram_gb`; GPL/AGPL solvers only as separate containers; drop FEniTop (stale, GPL) and JAX-FEM; preCICE couplings need multi-resource leases; defer GROMACS. |
| **Optimisation and surrogates** | Swap the CPU scikit-learn GP for GPyTorch/BoTorch in FP64 on the rig and add multi-fidelity. Replace the optimality-criteria update with **NLopt MMA** to unlock stress, buckling and modal constraints; torch-fem as the MIT differentiable cross-check. `optuna-mcp` plus `optuna-dashboard` on one Postgres-backed store seed the `gen_*` tools. Build these images from PyPI (AUR copies of OpenMDAO, SALib, nevergrad and Ax are stale). |
| **Manufacturing, CAM and robotics** | "CAM beyond Fusion" moves from later to v1: FreeCAD CAM with OpenCAMLib, SheetMetal unfolding, nesting, **CAMotics** as the CAM sign-off simulation. `aces make gcode` defaults to OrcaSlicer; Bambu Studio excluded (non-free networking plugin); CuraEngine stale on the AUR. ROS 2 Jazzy plus MoveIt as a container; **MotoROS2** adds streamed motion to the HC10DTP beside JBI files. Approval-gated `printer_*` MCP tools over Moonraker/OctoPrint. Metrology (Open3D, CloudCompare) puts as-built deviation into the ledger. |
| **Evidence and provenance** | The main gap: `ledger.jsonl` is an unsigned local hash chain with self-asserted timestamps. Day one: `cosign sign-blob` each bundle, an RFC 3161 timestamp on every append, an OpenTimestamps anchor of the daily head (only hashes leave the LAN); verify and timestamp status exposed through simlab-mcp. v1: BuildKit provenance and syft SBOMs pushed with ORAS and cosign to a LAN **zot** registry instead of `docker save | ssh`; signed models; WeasyPrint PDF/A-3 reports embedding `evidence.json`; pyHanko timestamps. Sovereign projects never touch the public Sigstore. lakeFS and immudb excluded (BSL). |
| **Agent layer and policy** | The broker becomes concrete: **ContextForge** (OPA rules, Cedar authorisation records) fronts every MCP server, each wrapped by Anthropic's `sandbox-runtime` over bubblewrap; **agentgateway** (Apache-2.0, Linux Foundation) is the alternate data plane. Sovereign mode needs a non-cloud default agent: **OpenCode on the DeepSeek lane**. No CAN, serial or PLC MCP server of any maturity exists, so `can_*`, `serial_*`, `plc_*` are ACES-written on FastMCP. Lane qualification with Inspect AI plus Harbor (Terminal-Bench runner) to reproduce DeepSeek's table. Traps: Crush (FSL), Konnect (AGPL), the unlicensed Onshape server, agent-scan needing a cloud token. |
| **Model serving and retrieval** | Before the day-30 benchmark: `llm-compressor` for the INT4 repack, Unsloth's ready V4-Flash GGUFs for ik_llama.cpp, a template-conformance check from DeepSeek's own encoding tests; measure with GuideLLM; gate with lm-eval plus Harbor. **LiteLLM** is the section 7 router and spend ledger and lets Claude Code use local lanes; `llama-swap` gives DARKNESS on-demand VRAM. Retrieval: Qdrant site-wide, LanceDB per project, Qwen3-Reranker, Docling ingest. Excluded: TGI, llm-guard, TensorZero, Milvus, LMDeploy, Axolotl. Presidio PII masking for Burst mode later. |
| **Creative and image/3-D** | **ComfyUI is GPL-3.0**: run it unmodified as a separate service with a source offer; keep ACES-owned code on `diffusers`. A licence-aware model registry: allow Apache/MIT weights (Qwen-Image, FLUX.2-klein-4B, FLUX.1-schnell, Z-Image, SAM 2.1, BiRefNet, TRELLIS.2), block non-commercial ones (FLUX dev family, RMBG-2.0, large Depth Anything), gate thresholds (SD 3.5 US$1 M, LTX US$10 M); a defence profile where the SAM licence and Hunyuan policy bar military use. Sovereign mode must disable cloud features inside `blender-mcp`, `krita-ai-diffusion` and InvokeAI. Ship the mature GPL apps (Blender 5.2, Krita 6, Inkscape 1.4, GIMP 3.2, darktable 5.6, Scribus 1.6) beside the alpha ArtCraft apps as separate processes, with their small community MCP servers. OpenColorIO ships an **ACES colour config**, which is the name collision in practice. |
| **Video and audio (the studio)** | Appendix A correction: the LTX-2 code, `ltx-trainer` and `ComfyUI-LTXVideo` are LTX-licensed like the weights, so they are fetched at install under the operator's acceptance; the Gemma 4 encoder is Apache-2.0. **Wan 2.2 (Apache-2.0)** is the video lane for customers above LTX's revenue threshold. Use `ltx-pipelines`' HDR/EXR and Dub-It paths before third-party tools. Audio: ACE-Step 1.5, Chatterbox, Kokoro; MMAudio and F5-TTS weights are non-commercial; Stable Audio Open is capped at US$1 M. C2PA and Perth watermarks extend the ledger to media. Editors: Kdenlive + MLT, FFmpeg, OBS; Olive is dormant. |
| **Field A: PLC, robotics, CAN** | python-snap7 v3 is pure Python (drop the C package); pymodbus is on the AUR; pyvisa-py means no NI-VISA; asyncua, asammdf, pyads and pycomm3 need ACES-owned PKGBUILDs. ROS 2 Jazzy via RoboStack/pixi because MotoROS2 ships Humble and Jazzy only; rehearse motion in Gazebo before approval. Write ACES's own broker-wrapped `plc_*` and `can_*` tools (community servers are toys). EtherCAT and real-time loops stay on a linux-rt bench PC. sigrok-cli and PulseView day one. Excluded: OpenPLC v3 (archived), python-OBD (GPL, idle), CaringCaribou. |
| **Field B: RF, firmware, device access** | Hand-verified after the agent sweep was blocked: GNU Radio, SoapySDR, rtl-sdr, HackRF, UHD, gqrx, SigDigger, URH, openEMS, Ghidra, rizin, binwalk, QEMU, OpenSSH, paramiko, Ansible, pyserial, python-can, can-utils, cantools, udsoncan, python-snap7, pyvisa, ROS 2 (AUR) with their licences and packaging. GPL tools run as separate processes; receive-only and read-only in v1 (section 5b). |
| **OS, packaging, security, operations** | Omarchy already ships Docker, LocalSend, ufw, a Tailscale panel, snapper with limine-snapper-sync (root snapshots on every update, not `/home`) and a stable Arch mirror about a month behind; archiso sits under omarchy-iso. ACES adds `/home` backup (evidence lives there: Omarchy Time Machine, restic), a customer-owned trust stack (Headscale, step-ca, OpenBao, Keycloak or Kanidm, Caddy, Warpgate), image signing plus SBOMs, agent sandboxing, Prometheus behind GRID CONTROL. No maintained Arch CVE auditor exists, so the compliance pack queries the Arch security tracker itself. Not adopted: Vault (BUSL), Grafana and Loki (AGPL; VictoriaLogs if needed), Netdata, Podman, plain WireGuard. |
| **Collaboration, PDM, knowledge** | The plan has no team layer; this supplies one without touching GPUs. Day one: **Gitea with LFS on the LAN as the PDM core**, Typst reports, Docling into the existing RAG. v1: an optional `aces-collab` compose stack behind Kanidm SSO; **StrictDoc** requirement IDs written into `aces.yaml` and `evidence.json` give requirement-to-run-to-report traceability with ReqIF for primes; InvenTree receives BOMs from `aces make`; Mattermost puts the model lane in chat. Honest PLM state: no active open PLM fits, so compose PDM rather than adopt one. Check Obsidian's redistribution terms before any ISO. |
| **Electronics (the known gap)** | A new pillar, `aces-electronics`: **KiCad 10** (Arch extra 10.0.7) with `kicad-cli`, `kicad-python` IPC and the KiCad MCP Pro read-only profile on day one, writes through the broker; ngspice 47; gerbonara; KiBot headless as evidence jobs. A **fabrication-release gate** before any upload: ERC 0, DRC with project rules, schematic-to-PCB netlist parity, vendor-verified footprints, defined stackup, a signal-integrity run (every one of these is a lesson from the AERIS-X64 audit). Uploading Gerbers to an offshore fabricator is a supply outside Australia under the Defence Trade Controls Act, so it needs a DSGL screen and recorded authorisation, approval-gated in every mode. |

**Two licence policies, not one (Aaron, 2026-10-09).** The auditor's rules below are the **distribution policy**:
they bind anything ACES ships to a customer (ISO, images, bundles, hosted lanes). On the operator's own installation the
**operator policy** applies: in `trial` mode every catalog row is installable, the audit's "exclude" and
"separate process only" placements become labels, and `aces policy override <component> --reason` records the
operator, the component's terms and the reason in the ledger. The first iteration runs in trial mode with everything
included, including FluidX3D and OpenSees; Aaron removes items as testing matures. **No restriction applies until an
end use is clear (Aaron, 2026-10-09):** every current project (HERMES, AERIS, PRISM media, the robot cell, the building
and vehicle work) is a hobby with no contract, no customer and no defence-industry engagement, which is the personal,
education and research use that non-commercial licences permit. The plan therefore has an **end-use gate**, not a
block: when a project gains a customer, a contract, a paid deliverable or a defence end use, its class changes and the
terms of every component it used are re-read at that moment (the ledger already records which components touched
which run, so the list is a query). The distribution policy exists only for a future shipped product; it filters
nothing today because nothing is being distributed.

**Licence audit (appended to the catalog).** The auditor reviewed the 111 rows whose licence is not plainly
permissive and wrote the distribution policy: permissive code ships and imports; GPL tools ship unmodified and are
called by CLI, socket or file, with any code that imports a GPL module living in a published GPL worker, never in
`aces-*` or paid code; AGPL services run in their own container, unmodified, with patches offered to network users
(hosted lanes included); as a commercial distributor ACES mirrors exact corresponding source per release and audits
built artefacts (SPDX SBOM plus ScanCode), not repository badges; weights come from an allow-list registry and anything
non-commercial, territorial or military-banned is blocked; unlicensed, BUSL, FSL and source-available code is excluded;
counsel clears H.264/HEVC patents, trademarks and LTX before sale. Twenty-four rows are excluded outright, among them the
unlicensed Onshape server, TetGen, MeshLib, FEniTop, JAX-FEM and TopOpt_in_PETSc (a GPL or unlicensed import would make
gendesign GPL), Bambu Studio, lakeFS, Crush (FSL), the Stability and Tencent model families, FLUX.1 dev, BRIA RMBG, and
the non-commercial weights of F5-TTS and MMAudio. Fifty-seven rows ship only as separate processes or containers.

**Completeness critic (appended to the catalog).** The catalog is broad on CAD kernels, numerics, evidence, model lanes
and media but thin where customer-zero work lives. Seventeen additions to consider before v1, each with a permissive
licence unless noted: OpenCV as the machine-vision base for the robot cell; PX4 SITL with MAVSDK, QGroundControl and
JSBSim as the twin-first path for the AERIS and Peregreen UAV work; Nav2 and slam_toolbox for HERMES inside the ROS 2
container; Rerun and MCAP as one viewer and one hashable log container for bags, CAN, camera and SDR captures; ParaView
with trame for post-processing inside the browser-first console; OpenModelica and python-control for system and
controls design; Mosquitto, Sparkplug B and TimescaleDB for field telemetry; DuckDB, Arrow and Zarr for analytical data
across sweeps and ledgers; AeroSandbox, OpenVSP, pyCycle, Dymos and OpenAeroStruct for aero and propulsion design;
Cantera, CoolProp and a flowsheet simulator for the coupled-combustion and PRISM work; IfcOpenShell, QGIS and PDAL for
the AEC projects; OpenTURNS, Pint and handcalcs for uncertainty, unit-checked parameters and auditable calc sheets;
Zotero (AGPL) for the per-project prior-art record that R&DTI claims need; Capella or SysON for SysML v2 when defence
primes ask for it; OSS Review Toolkit and ScanCode for notices and source offers; auditd and Lynis for host hardening
evidence; Zephyr and PlatformIO as the firmware build system beside the flashing and emulation tools. The critic also
asks for one winner per overlap before v1 (one policy engine, one gateway, one ROS 2 packaging, Typst or WeasyPrint,
Qdrant over LanceDB, Unsloth over LlamaFactory, MFEM primary with CalculiX for independent sign-off, gmsh over Netgen,
build123d over CadQuery, one of Mattermost or Zulip) and notes that already-decided components (gmsh, PyFR, trimesh,
the ArtCraft apps, the Fusion bridges, the planned solvers) have no catalog rows yet, so the licence register must be
built from the packages actually shipped, not from the catalog alone.

## Appendix A. Licence matrix

| Component | Licence | Product consequence |
|---|---|---|
| Omarchy | MIT (name unstated) | Build on it; own branding; attribute |
| ArtCraft seven apps | MIT OR Apache-2.0 (4 of 7 licence files read; FP §5) | Redistribute as packages; read the other three; attribute |
| LTX-2.5 + first-party adapters | LTX-2.x Community License (free < US$10 M whole-entity revenue; paid above; fine-tune transfer terms) | No redistribution of weights; operator acceptance; revenue attestation |
| Gemma 4 12B text encoder (LTX component) | *unread* | Read before the studio ships externally |
| GLM-5.2, GLM-4.7-Flash | MIT | Serve freely |
| DeepSeek-V4-Flash-0731, V4.1-Flash | MIT | Serve freely |
| PyFR, build123d, gmsh, MLflow, Optuna, CuPy, Warp, PhysicsNeMo, ComfyUI | permissive / open (ComfyUI is GPL-3.0: run as a separate service, do not link) | Ship in images; keep notices; publish copyleft sources in the bundled mirror |
| Field packages (GNU Radio, SoapySDR, Ghidra, rizin, python-can, ROS 2 …) | various open licences, some GPL | Separate processes and containers; licence register entries |
| FluidX3D | custom: free for public research, education or personal use; no commercial use; no military or defence-industry use; no AI training on its source (LICENSE.md read 2026-10-09) | **Included in the first iteration, unrestricted on the current hobby projects** (decisions 6 and 7); terms re-read at the end-use gate |
| OpenSees | custom, non-commercial and internal use only | **Included in the first iteration, unrestricted on the current hobby projects**; terms re-read at the end-use gate |
| The 24 rows the audit excluded and the 57 it limited to separate processes | various (see `research/INTEGRATIONS.md`) | Installable in `trial` mode with their terms as labels; the distribution policy filters a future shipped product only |
| Fusion | commercial subscription; official MCP needs it | Customer's licence; VM lane or CAD node |
| Onshape | commercial plans; official MCP via the App Store | Customer's account |

## Appendix B. Sizing notes (derived from config.json files, ±5 %)

| Model | Layers | Experts (routed + shared) | Active experts | Hidden | Total | Active |
|---|---|---|---|---|---|---|
| GLM-5.2 | 78 (3 dense) | 256 + 1 | 8 | 6144 | ≈ 750 B | ≈ 40 B |
| DeepSeek-V4-Flash-0731 | 43 | 256 + 1 | 6 | 4096 | ≈ 290 B | ≈ 13 B |
| DeepSeek-V4.1-Flash | 40 (CED 20+20) | 384 + 1 | 6 | 5120 | 552 B + 196 B Engram | 8 B prefill / 16 B decode |

Four CMP 170HX cards: 256 GB total, about 230 GB usable after runtime overhead, each card capped at 60 GB by the lease
rule. One LTX-2.5 worker per card: int8 distilled DiT 21.5 GB + int8 Gemma4-12B encoder + VAEs fit in 64 GB; bf16 DiT
(42 GB) plus bf16 encoder does not.

## Appendix C. Questions the panel left open

- Does Omarchy's `cidata` flow accept extra packages, or is an installer patch needed?
- Does the nvidia-open DKMS module for the RTX PRO 4000 build against `linux-omarchy`?
- Do DSpark speculative decoding and an INT4 DeepSeek-V4-Flash-0731 work on sm_80 under pipeline parallel?
- Which Fusion licence covers the VM lane (the community bridge is licence-agnostic, but Fusion itself is not)?
- May the unlocked CMP rig run paid Programs under NVIDIA's terms?
- Where does the project store live that both boots and the rig mount?
- Does the pacman PreTransaction snapshot hook coexist with Omarchy's own system snapshots?

## Appendix D. What the panel changed between Rev A and Rev B

- **Grafted from the architect draft (the winning spine):** the pinned-mirror-snapshot layering; `aces-fabricd`,
  `aces-router` with logical model names and no silent cloud fallback; `aces-devd`; one MCP broker with a consequential
  tier, physical-presence approvals and expiring single-action grants; project classes driving routing and egress;
  nftables per-uid egress policy; the ledger extended to tool calls, approvals, device transcripts, egress and updates
  and verified at post-boot; the selftest-and-rollback update path with rc / edge / stable roles; `aces.*` plugins;
  moving always-on services off the dual-booting workstation.
- **Grafted from the product draft:** Programs as the wedge and first revenue; stock hardware for the Server SKU with
  FP64 per dollar as the metric; the open evidence verifier as an adviser channel; validated digests kept for the
  subscription term; the licence register and SBOM; three customer segments; the positioning table with an explicit
  no-parity claim; the launch sequence and proof-point list; pricing inputs; the question of which entity sells.
- **Corrections from the judges:** previews on DARKNESS are evidence (preview-grade), not "non-evidential"; tensor
  parallel over PCIe Gen 2 is slow, not impossible; openEMS is installable but not validated, Palace and gprMax are
  planned, so RF simulation is not evidence-grade yet; PyFR is validated only on the Re-200 cylinder, so the aerospike
  sweep needs its own validation case; GPU 6 is soaked before its flag is cleared; ArtCraft moves to days 0–30; the
  broker v0 moves to days 1–30; the lawyer moves to days 31–60; ROS 2 ships as a container, not an AUR rebuild; CAD
  adapters and the video studio are "build", not "wire"; the field pack is screened, not part of a free image; R&DTI
  eligibility is the customer adviser's call; the claim that firms keep no verifiable design records was dropped.
- **Re-run on another model:** the engineer-workflow and pre-mortem drafts were first blocked by the API's cyber
  safeguard (the brief's field-tooling section triggers it) and then completed on a second model with tighter caps;
  both critics stayed blocked. **Grafted from the engineer's draft:** the `CadSource` contract, the preflight lint,
  the independent second discretisation for sign-off, measurements as evidence, the digital thread and offline
  verifier, `diagnose-case`, nightly sweeps on the submit tier, the voxel kernel as a content-addressed field kernel
  (section 4, pillar 7), LTX renders as leased jobs (open question 18), the Fusion API corpus behind `rag_search`.
  **Grafted from the pre-mortem:** engine first and OS optional with all Omarchy coupling in `aces-omarchy`; the
  overlay-not-fork install path; the release train with a nightly edge canary, an Arch Archive date pin and forked,
  pinned AUR recipes; the offline-first signed supply chain; capability-keyed lanes and `aces lane test`; twin-first
  device access with presence-based approvals; the "ACES 0.1 Bench" scope with day-14/30/45/90 gates and tripwires;
  the sellable unit as Server plus OS-agnostic clients; the name collision with the Academy Color Encoding System;
  open questions 13 to 18.
