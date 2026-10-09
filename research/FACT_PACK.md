# ACES OS — fact pack for planning (2026-10-08)

Purpose: the single brief for anyone (human or agent) drafting the ACES OS vision. Everything here was
read from a primary source today unless marked *(unverified)* or *(derived)*. Do not invent numbers,
customers, prices or testimonials; where a fact is missing, say so. Standing rule from the owner: no
fabricated or sample data anywhere, ever.

Owner: Aaron (Carbon Project Australia / Omniscient AI), workstation "DARKNESS", rig at 192.168.1.65.

---

## 1. The brief (Aaron, 2026-10-08, verbatim intent)

1. ACES = **Advanced Computational Engineering Software**. It is "so core to how I want my workflows and
   processes to function" that it becomes a **whole operating system**.
2. Base: **Omarchy 4 "Quattro"** (DHH's Arch + Hyprland distribution, "heavily skewed towards agentic").
3. Built in, as part of the OS: all the computational / simulation-driven work, **the voxel CAD**, the
   **agentic CAD Fusion adapters**, **Onshape adapters**, all integrations.
4. **Sellable product**: the entire thing packaged up. "We run the servers that feed the open-weight
   models, or if the customer chooses any model they want." Customer-chosen models must be possible.
5. First iteration serves Aaron: **GLM-5.2 on four of the rig's GPUs; the other four allocated to
   simulation and solvers.** The server is the compute for the OS; the OS runs on DARKNESS.
   - Follow-up (same session): "if GLM 5.2 is impossible on those four lanes, I believe we can use
     DeepSeek Flash 4.1 for four cards." (see section 7 for the sizing: GLM-5.2 does not fit four
     cards; DeepSeek-V4-Flash-0731 does; V4.1-Flash is marginal.)
6. Day-one installs: **all seven ArtCraft "Crafting Apps"** — PhotoCraft, VectorCraft, FilmCraft,
   LightCraft, PrintCraft (now published as PdfCraft), EffectCraft, DesignCraft.
7. **LTX Studio, or a version of it, built in**: that is how the server generates video, including
   **all of the LTX LoRAs and trained adapters** for enhancement and specific functions.
8. Deliverable wanted now: the vision — how it comes together, the feature sets, and how it becomes a
   sellable product.

Related standing positions (from earlier sessions, still binding):
- Generative design over standard drafting; open solvers are the engine; Fusion's cloud GD is at most
  a benchmark.
- Every run is R&D evidence (R&DTI-grade, hash-chained ledger). "Surrogates rank, the real solver signs off."
- Mode switching on the rig stays human (GRID CONTROL); agents submit and read only.
- No fabricated data in any tool or UI; honest empty states.
- Omniscient AI (sister business) sells a sovereign on-prem "AI workforce in a box" to AU mid-market
  under a 3-year lease ("own the intelligence, lease the appliance"); R&DTI 43.5 % refundable offset is
  a third value lever; compliance pack must be built with an AU lawyer. ACES OS can reuse that commercial
  machinery but must not inherit its design system (Omniscient has its own; ACES/SIMLAB has "Parameter Sky").

---

## 2. What already exists (the ACES platform today)

Repo `C:\Users\shedm\cudax-sim-runtime` (not under git yet). Handoff: `ACES_HANDOFF.md` (2026-10-06).
Positioning adopted: the self-hosted equivalent of LEAP 71's Noyron ("Large Computational Engineering
Model": physics-true generators → wide sweeps on owned FP64 GPUs → surrogates → optimiser → verification
on the real solver → evidence). Noyron facts checked 2026-10-05: 5 kN kerolox aerospike hot-fired
18 Dec 2024; two 20 kN methalox engines hot-fired, announced 11 Dec 2025.

Built and verified (workstation and/or rig):
- `gendesign/`: GPU voxel H8 FEA (matrix-free Jacobi-PCG, **float64 by default** after an FP32
  false-convergence finding), 3-D SIMP topology optimisation, Optuna TPE/NSGA-II parametric loop over
  build123d or Fusion parameters, Angelino aerospike nozzle generator (CSV/PNG/STEP, RocketCEA hook),
  Fusion bridge (STL/STEP into Fusion over the community add-in :9876), Warp differentiable rod-lattice
  FEA (gradients exact after a kernel split), lattice optimiser.
- `simlab/`: experiment.yaml (pydantic) → sweep fan-out → staged jobs (mesh cpu → solve sim → post cpu)
  on redis/rq with **one GPU lease per case** (rig-state idle ∧ GRID CONTROL not derated ∧ active-mode
  pool ∧ no lease), MLflow + Postgres tracking, evidence.json per run (input hashes, image digest, GPU
  uuid/serial/PCI), experiment bundles + hash-chained ledger (`simlab evidence build|verify`), delivery to
  Nabu's evidence API (two real records live), reports md/html/docx/pdf, `simlab export` (Fusion → STEP +
  parameter snapshot + sha256 + experiment.yaml), gmsh mesh stage, STEP → voxel FEA solver.
- `simlab-mcp` :8020 (streamable HTTP; tiers read/submit/full via `SIMLAB_MCP_POLICY`; bearer tokens →
  principals; audit log; tools rig_capacity, sim_list_solvers, sim_validate, sim_submit, sim_sweep,
  sim_status, sim_results, sim_report(_files), sim_cancel, sim_evidence, sim_list_experiments; **no mode
  switch tool by design**).
- Solver registry `simlab/solvers.json` (each entry carries a validation record tied to its image digest):
  gendesign-topopt, gendesign-beam-check (−1.30 % / −0.84 % vs Timoshenko), gendesign-parametric,
  step-structures, nozzle-angelino (throat-area check 1.0000), pyfr (Re-200 cylinder St 0.2015, Cd 1.354),
  surrogate GP / extra-trees / FNO (FNO held-out rel-L2 VM 0.072, |u| 0.047; peaks under-predicted
  3.5–10.9 %, hence "surrogate never gets the final word"; 10 ms vs 2.4 s per design).
- Images (built on the workstation, shipped by `docker save | ssh docker load` because the rig has no PyPI
  route): gendesign:0.3 (12.8 GB), simlab-pyfr:0.3 (17.1 GB), simlab-ml:0.1 (20.3 GB; torch 2.11+cu128,
  PhysicsNeMo 2.2.2, Warp 1.18), simlab-queue:0.3, simlab-mlflow:0.2. Container CUDA must be ≤ 13.3.
- Operator UI: **SIMLAB console** :8030 (Starlette + static ES modules, Three.js r170 vendored, no build
  step; plates Fleet / Pipeline / Objects / Solvers / Evidence) embedded as the Simulation plane of
  **GRID CONTROL rev B** (single HTML file served at :3200). Design system "Parameter Sky" (navy base,
  cyan = live, amber = modes and actions, red-orange = fault only; parametric node-tool register; no
  decoration without data). Recorded in `PRODUCT.md`, `DESIGN.md`, `.impeccable/`.
- Planned, not built: MFEM+cuDSS, OpenFOAM+OGL/AmgX, NekRS, XLB/OpenLB, DualSPHysics, Newton/MuJoCo Warp,
  Chrono, Palace, gprMax, FEniCSx, LAMMPS/GROMACS, FEniTop/TopOpt_in_PETSc; Heaviside projection,
  multigrid PCG, stress/buckling/modal objectives, manufacturing filters, `gen_*` MCP tools; coupled
  combustion/thermal; manufacturing-aware generation; hardware validation.
- Licence exclusions already decided: FluidX3D (bans commercial, defence and AI-training use), OpenSees
  (non-commercial), cuPyNumeric (EOL); Isaac Sim/Omniverse excluded on the rig (no RTX renderer); no
  multi-GPU domain decomposition (no P2P); no Slurm.

Adjacent assets that belong in the OS story:
- **formgen** (`~\formgen`): Fusion forms → Branch-Technology-style tessellated lattices → robot `.JBI`
  jobs (HC10DTP/YRC1000micro) and printable scale models; GUI + CLI + Fusion add-in; speaks the :9876
  bridge protocol directly (newline-delimited JSON `{"type":"execute_code","params":{"code":...}}`).
- Global Claude skills: `cad` (build123d STEP/STL/3MF/GLB, headless), `dxf`, `cad-viewer`, `step-parts`,
  `urdf/srdf/sdf`, `gcode`, `bambu-labs`, `sendcutsend`, `implicit-cad`, `cad-electronics` (router;
  electronics = read-only Fusion Electronics, KiCad/SPICE gap), `fusion-cad-agent`, `engineering-viz`.
  Python libs: build123d, trimesh, PyniteFEA, thermo, CuPy, Optuna, PhysicsNeMo, Warp.
- Catalogs: aero-sim-catalog (357 verified solvers/datasets, ASL-SIM-001) and mfg-aec-catalog (384,
  MFG-AEC-001) with licence traps recorded.
- GLM-5 CAD corpus (`F:\glm5-cad-corpus`, ~3 M instruction pairs processed; Fusion API reference
  17,993 records) for a future engineering fine-tune — harvest/refine done, training not started.
- Agent runtimes on the workstation: Nabu (Next.js command center :3001, company-segmented, evidence
  API, Capability Lab), OpenClaw gateway :18789, Hermes CLI, OpenFang daemon :4200, prime-agent (Docker),
  Jarvis voice assistant; local models via Ollama / vLLM. Agent RAG knowledge base (qdrant :7801,
  622,863 chunks). Many of these are Windows-hosted today.
- Omniscient AI sales/finance kit (pamphlet, 1-pager, booklet, SLA template, value calculator, finance
  model) — reusable templates for the ACES OS commercial package.

---

## 3. Hardware

### Rig (192.168.1.65, Linux/systemd, LAN only; SSH from the workstation currently denied)
- 8× NVIDIA CMP 170HX unlocked to **65,536 MiB each** (GA100, sm_80, 70 SMs). Measured per card:
  **FP64 GEMM 11.34 TFLOPS** (≈ 90 across 8), FP32 12.38, FP16 tensor 186, D2D 1,585 GB/s, H2D pinned
  6.63 GB/s. **PCIe Gen 2 ×16**, no NVLink, no P2P, no display outputs, 250 W cap. Third-gen tensor
  cores: **no FP8, no FP4 hardware** (FP8/NVFP4 checkpoints must be dequantised or repacked to INT4/GGUF).
- Host: dual EPYC, 192 threads, **1 TB RAM**, `/` 3.75 TB NVMe, `/mnt/nvme2` 3.75 TB.
- Driver nvidia-open 610.43.03 (unlocker-patched; container CUDA ≤ 13.3). **Freeze rule:** never install
  or upgrade nvidia-*/kernel/CUDA on the host, never run nvidia-smi mutators; containers only; apt holds.
- GRID CONTROL :3200 (Aaron's operator shell; modes, telemetry, mission-control agents). Modes that exist
  today (read 2026-10-08): cad-engineering; general-fleet (3× Apodex); **general-flagship (GLM-5.2
  ik_llama.cpp GPU-offload, cards 0-5+7 = seven cards)**; rag-training; batch-overnight; hybrid-big-model
  (CPU flagship); **video-studio (LTX-2.5 ×6 + editor + director)**; general-fleet-v2 (2× 122B PP2 +
  2× GLM-4.7-Flash + GPT-OSS-20B); redteam-mission; **sim-lab-4 (GPUs 0-3 simulation; GLM-Flash on 4,5 +
  GPT-OSS on 7; 6 rests)**; sim-lab-8 (all GPUs simulation, no local LLM).
- State 2026-10-08: mode sim-lab-4 (detected "transition"), GPUs 0-3 and 6 empty, GPUs 4/5/7 hold
  ~56–58 GB each (vLLM lanes). rig-state MCP :8010 (read-only) alive; MLflow :5000 up; simlab-mcp :8020
  unclear; SIMLAB console not deployed on the rig; vLLM apodex/tools/strategist services inactive;
  embed-cpu (Qwen3-Embedding-0.6B) active on :8005.

### DARKNESS (this workstation)
- Windows 11 Pro for Workstations today; NVIDIA **RTX PRO 4000 Blackwell SFF, 24 GB** (FP64 at 1/64
  rate — dev/preview node only); Fusion 360 installed with both MCP routes; Docker Desktop (WSL2);
  Nabu and the agent runtimes above. Aaron intends ACES OS (Omarchy-based) to run **on this computer**.
  Consequence: Fusion (Windows/macOS only) can no longer run natively on the same box once it boots Omarchy
  (see section 8 for options: Omarchy's built-in Windows VM, dual boot, or a separate Windows CAD node).

---

## 4. Omarchy 4 "Quattro" (verified 2026-10-08 from omarchy.org, GitHub omacom/omarchy, release notes)

- Tagline: "Beautiful, fun & agentic Linux by DHH", "The malleable OS for the age of agents". Creator
  David Heinemeier Hansson; shipped by 37signals; **MIT licence** (LICENSE file: "Copyright (c) David
  Heinemeier Hansson … without restriction, including … sell copies"). GitHub org moved to **omacom/omarchy**
  (basecamp/omarchy redirects). Alibaba Cloud joined as a Founding Corporate Patron ($3 M, Sep 2026).
  Omarchy has a core team and a security team with responsible disclosure.
- **Versions:** 4.0.0 "Quattro" shipped 14 Aug 2026; latest **4.0.4 (15 Sep 2026)** which makes the
  bespoke **`linux-omarchy` kernel** the default. 4.0.3 (8 Sep) added **OpenClaw** (desktop app + default
  coding agent + local user gateway) and **Hermes Desktop** integration, plus Cursor CLI and Muse Code.
- **Desktop shell:** the whole shell is one long-running **Quickshell (QML) process `omarchy-shell` with a
  plugin architecture**; Waybar, Walker, Mako, SwayOSD, hyprlock, hypridle, swaybg, polkit-gnome were all
  deleted. Under ~300 MB RAM. Event-driven status. Theme palette 24 colours; themes restyle terminal,
  bar, notifications, wallpaper, editors, and the agents (Claude Code, Pi, OpenCode, Hermes follow theme
  changes). Hyprland config is Lua. Default terminal foot (Alacritty, Ghostty, Kitty supported).
  First-party apps Omawrite, Omacut, Omacalc (C++ / Qt Quick) built the way the `omarchy-app` agent skill
  teaches; Hype, Monologue mentioned as first-party too.
- **Packaging:** internals moved from a git checkout to **pacman packages** (`/etc`, `/usr/share/omarchy`);
  updates via `omarchy update` / pacman. The desktop depends on `quickshell-git` (tracks upstream git).
- **ISO:** repo `omacom/omarchy-iso`; `./bin/omarchy-iso-make` builds from the stable mirror (branch
  `quattro`), `--local-source ../omarchy-installer ../omarchy-pkgs` builds from sibling checkouts;
  `--edge/--dev/--rc` channels. The ISO ships the Omarchy Configurator, installs Arch, installs Omarchy
  packages from a **bundled mirror inside the ISO**, runs setup in chroot, creates the user. ISO < 6 GB;
  installs 35 s on fast machines. Dual boot beside Windows (free-space install, LUKS kept).
- **Unattended installs:** a second drive labelled `cidata` (cloud-init NoCloud) with
  `user_configuration.json`, `user_credentials.json` (+ optional `authorized_keys`, `tailscale_authkey`,
  full name, email, encrypt flag) makes the ISO install itself; `defer-provisioning` prepares a machine for
  another owner. Proxmox / libvirt / Packer friendly. (Appliance imaging path for a product.)
- **Agents are system citizens:** every major coding-agent CLI is pre-wired as a lazy mise stub
  (`claude`, `codex`, `opencode`, `agy`, `copilot`, `crush`, `grok`, `pi`, `omp`, `ori`, `hermes`, `muse`,
  `cursor-agent`); `omarchy default agent <name>`; `Super+Shift+Ctrl+A` launches it; `omarchy agent prompt
  "…"` fires a task unattended (auto-approve modes); agents panel in the bar shows every subscription's
  5-hour and weekly limits, tokens by day and by model, multi-account switching with auto-switch at 95 %;
  usage JSON in `~/.local/state/omarchy/agents/usage/` (cross-machine aggregation via a synced folder);
  **crash diagnosis**: systemd-coredump → notification → core dump handed to the agent with a
  `diagnose-crash` skill; the shipped **Omarchy skill** (symlinked into `~/.claude/skills`,
  `~/.codex/skills`, `~/.pi/agent/skills`, `~/.agents/skills`) teaches agents to reconfigure the machine
  (hard line: `/usr/share/omarchy` read-only, `~/.config` yours) — marked experimental by the manual.
  "Make something" starter prompts for a theme, plugin or app. Herdr multiplexer watches agents;
  tmux layouts `tdl/tdlm/tsl`. Install > AI menu (OpenClaw, Hermes, Perplexity, T3 Code…).
- **Hooks:** executable scripts in `~/.config/omarchy/hooks/<event>.d/` (battery-low, font-set,
  post-boot, post-update, pre-refresh-pacman, theme-set).
- **Shell plugins:** directory with `manifest.json` (`schemaVersion: 1`, `id`, `name`, `version`, `kinds`,
  `entryPoints`) + QML. Kinds: `bar-widget`, `panel`, `overlay`, `menu`, `service`, `bar`. First-party in
  `$OMARCHY_PATH/shell/plugins/` (trusted interfaces); third-party in `~/.config/omarchy/plugins/` (limited
  interface scoped to own service/lifecycle); `omarchy plugin list|clone|validate|add|remove`; hot reload
  on save; distribution = any public git repo + listing at plugins.omarchy.org ("thousands of community
  plugins"). Namespace `omarchy.` reserved.
- **Web apps:** _Install > Web App_ wraps any URL in a frameless window with launcher entry and hotkey
  (how SIMLAB / GRID CONTROL / MLflow / ComfyUI consoles would appear as first-class apps).
- **Windows VM:** _Install > Windows_ runs Windows in a Docker-based KVM VM (console at :8006, RDP full
  screen with sound, clipboard, scaling; `omarchy windows vm launch|stop|status|--keep-alive`; file
  sharing). No mention of GPU passthrough → Fusion in that VM = software rendering *(implication)*.
- **Branding:** `omarchy plymouth set <bg> <fg> logo.png` rebrands boot unlock + SDDM; screensaver and
  About screen take a logo (`~/.config/omarchy/branding/`). (A derivative can carry its own identity;
  the Omarchy *name* is a trademark question — MIT covers code, not the mark *(check)*.)
- Also: system snapshots, LUKS, ufw default-deny except LocalSend, fingerprint/hardware auth, Mac
  support, "potato" PCs (2011 X220 2 GB), gaming, Tailscale join at install.
- **Omarchy Doctrine** (ten principles): Unite the nerds · Hold the line · Have some fun · Beauty is
  truth · Heritage is duty · Command is service · **Welcome the agents** · Perfect the computer ·
  **Own the machine** ("no tollbooths, no gatekeepers") · You're somebody now.

---

## 5. ArtCraft "Crafting Apps" (verified 2026-10-08: getartcraft.com/apps, GitHub storytold/*, AUR)

- Seven native apps, **pure Rust** (no Electron/web views), GPU compositor on **wgpu** (Metal/Vulkan/
  DX12/WebGPU), macOS/Windows/Linux (+FreeBSD badge), six of them also compile to **WebAssembly**.
  Licence: **MIT OR Apache-2.0** (LICENSE-MIT + LICENSE-APACHE present in photocraft, vectorcraft,
  filmcraft, effectcraft; site says "permissive licenses" for all). Built by an AI agent fleet (press:
  "between September 30 and October 1" 2026, by @echelon); community on Discord; "clean-room"
  reimplementations of Adobe apps.
- **Agent-ready by design:** "Drive every app from a CLI, a JSON control channel or an MCP server"
  (PhotoCraft README: "Every action is a command"; screenshots rendered offscreen through its control
  channel). PhotoCraft reads/writes real PSD (307/309 psd-tools test files).
- Status (site): PhotoCraft **early alpha**, PdfCraft **early alpha**; VectorCraft, FilmCraft, LightCraft,
  EffectCraft, DesignCraft **in development** — all "installers ready" (AppImage on Linux, MSI, macOS
  universal). **PrintCraft was renamed PdfCraft** on the site; AUR still carries `printcraft` 0.2.1 and
  `pdfcraft-git`.
- **AUR packages exist today** (source `-git`, source tagged, and `-bin`): photocraft 0.3.0, vectorcraft
  0.4.0, filmcraft 0.2.1, lightcraft 0.2.1, printcraft 0.2.1 / pdfcraft-git, effectcraft 0.4.0,
  designcraft 0.2.1 (+ `artcraft` 0.41.0 studio). So an Arch-based OS can ship them as pacman packages
  from its own repo (rebuild from source for provenance).
- Separate umbrella product **ArtCraft studio** (storytold/artcraft, AUR `artcraft`): "IDE for interactive
  AI image and video creation" — compositing (2-D/3-D, posing, image→3-D mesh, background removal) that
  renders through **cloud models** (site lists Seedance 2.5, Nano Banana 2) with a web app at
  app.getartcraft.com; licence file LICENSE.md (terms not read). Not in Aaron's list; relevant only as an
  optional front-end, and it conflicts with sovereignty unless pointed at local models *(assessment)*.
- Risk notes for a product: alpha quality, vibe-coded (press framing), fast-moving versions, no
  support organisation; shipping them "day one" means ACES owns QA, packaging and update cadence.

---

## 6. LTX-2.5 (verified 2026-10-08: HF Lightricks/LTX-2.5 model card, GitHub Lightricks/LTX-2)

- **LTX-2.5** (released 23 Jul 2026, updated 2 Oct 2026): Lightricks' **open-weights audio-video world
  model**, **22B DiT**; text/image/video → synchronized video + audio; native multishot; Diffusion
  Fidelity Rendering (DFR); new diffusion video decoder; **Gemma 4 12B** text encoder; prompt enhancer;
  duration predictor; improved distilled checkpoint. Day-0 support in ComfyUI. 1.7 M downloads.
- **Licence: "LTX-2.x Community License Agreement"** (license: other). Model card: **under US$10 M annual
  revenue → commercial and production use at no cost; transfer of fine-tunes may require a paid licence;
  over $10 M → paid Commercial Use Agreement** (full weights, support, LoRAs, flexible deployment).
  "Revenue is measured across the whole entity, including subsidiaries and affiliates under common
  control." Binding terms: github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x. HF repo is gated (accept
  terms). **Implication for a sellable OS:** fine for Aaron's entities today; a customer over the
  threshold needs their own LTX commercial agreement; redistributing weights/fine-tunes inside a product
  needs a lawyer's read of LICENSE-2_x *(assessment)*.
- **Checkpoint pack (split, Comfy-aligned):** distilled DiT bf16 **42.0 GB** (fixed 8-step, CFG 1), dev DiT
  bf16 42.0 GB (trainable), Comfy int8-convrot variants **21.5 GB** (ComfyUI only), distilled **NVFP4 18.7
  GB** (Blackwell only — not usable on GA100), Gemma4-12B text encoder bf16 (+ int8 Comfy), video VAE
  (DiffVAE heavy / conv light), audio VAE + vocoder, distilled LoRA 450 (8.9 GB), duration head, x2 spatial
  and temporal latent upscalers. Distilled quick-start download ≈ **66 GiB**. Python ≥ 3.12, CUDA ≥ 12.7,
  PyTorch ~2.7; `ltx-pipelines` package (DistilledPipeline, DFR pipeline, `--quantization fp8-cast
  --offload cpu|disk` for memory-constrained GPUs).
- **First-party LoRA / adapter library (all `license: other`, base LTX-2.5):** IC-LoRA Pixel-Spatial-
  Upscaler (the DFR detailing LoRA), Refine-Details, SDR-To-HDR, Restore (archive footage/colorization,
  base LTX-2.3), Colorization, Deblur, Decompression, Clean-Plate (object removal), Alpha-Gen (matting),
  Layout-To-Render (camera control), Water-Simulation (VFX), Ingredients (reference sheet / character
  consistency); LoRA Slow-Motion-Control, Cinemagraph. ("All of the LTX LoRAs and trained adapters" in
  the brief = this family plus Aaron's own trained adapters.)
- **Training:** LTX-2.5 is "built for local execution and fine-tuning" (dev transformer is the trainable
  one); third-party local LoRA trainers exist (e.g. OmniChar: clip LoRAs on video on your own GPU).
  Lightricks' own trainer location in the LTX-2 repo not read today *(unverified)*.
- **Fit on a 64 GB GA100 card** *(derived)*: distilled DiT bf16 42 GB + Gemma4-12B bf16 ~24 GB exceeds
  64 GB together → use the Comfy int8 DiT (21.5 GB) + int8 TE, or keep the TE on a second card / CPU
  offload, or `fp8-cast` weight-only. The rig's existing **video-studio mode runs LTX-2.5 ×6** → one worker
  per card already works there (exact config not read). GA100 lacks FP8/NVFP4 hardware; bf16 compute.
- Lightricks' commercial web product is **LTX Studio** (app.ltx.studio); the open stack is LTX-2 +
  ComfyUI + `ltx-pipelines`. "A version of LTX Studio built in" = an ACES-owned video studio surface over
  the open stack, not Lightricks' SaaS *(interpretation)*.
- Workstation: LTX Desktop (Windows app, `%LOCALAPPDATA%\LTXDesktop`) has held the 24 GB GPU since
  2026-08-30 (standing hold; Ollama/vLLM kept off).

---

## 7. LLM lanes on the rig: what fits four 64 GB cards (256 GB total, ~230 GB usable)

Sizes derived from each model's `config.json` (2026-10-08); treat as ±5 %. GA100 has no FP8/FP4 tensor
cores, so FP8/FP4 checkpoints are served either dequantised to bf16 compute (weight-only kernels such as
Marlin/AWQ/GPTQ-INT4, GGUF Q4 via ik_llama.cpp/llama.cpp) or via KTransformers CPU/GPU hybrid.

| Model | Licence | Total params | Active / token | Weights at ~4-bit | Fits 4 × 64 GB? |
|---|---|---|---|---|---|
| **GLM-5.2** (zai-org, Jun 2026; `glm_moe_dsa`, 78 layers, 256 routed + 1 shared experts, 8/tok, 1 M ctx, IndexShare sparse attention, MTP) | MIT | ≈ **750 B** *(derived)* | ≈ 40 B | ≈ 375 GB | **No.** Needs ~7 cards (the existing `general-flagship` mode uses cards 0-5+7 with ik_llama.cpp), or KTransformers with most experts in the 1 TB host RAM (slow, unbenchmarked). FP8 official ≈ 750 GB. Successor listed: GLM-5.3-BF16. Official local stacks: SGLang, vLLM, Transformers, KTransformers, Unsloth. |
| **DeepSeek-V4-Flash-0731** (Jul 2026; 43 layers, 256 routed + 1 shared, 6/tok, hidden 4096, 1 M ctx, DSpark speculative decoding) | MIT | ≈ **290 B** *(derived)* | ≈ 13 B | ≈ 145–165 GB (FP4 experts as released ≈ 139 GB + FP8 attention; GGUF Q4_K_M ≈ 165 GB) | **Yes**, with ~70–90 GB left for KV cache. DeepSeek's own table: Terminal Bench 2.1 **82.7 vs GLM-5.2 81.0**, NL2Repo 54.2 vs 48.9, DeepSWE 54.4 vs 46.2, Toolathlon 70.3 vs 59.9 (Opus-4.8 85.0 / 69.7 / 58.0 / 76.2). vLLM `--speculative-config dspark`. No Jinja chat template (own `encoding` scripts). |
| **DeepSeek-V4.1-Flash** (Sep 2026; multimodal, CED 20+20 layers, 384 routed + 1 shared, 6/tok, Engram memory 196 B sparsely accessed, 1 M ctx) | MIT | **552 B backbone + 196 B Engram** (model card) | 8 B prefill / 16 B decode | ≈ 273 GB experts (released FP4) + Engram tables | **Marginal/no on four cards** fully resident; ~5–6 cards, or ~25 % of experts in host RAM (KTransformers-style) with Engram in RAM. KV cache is tiny (890 B/token). |
| GLM-4.7-Flash, GPT-OSS-20B, 122B-class models | various | small | — | — | Already run as vLLM lanes on single cards (current sim-lab-4 keeps them on 4/5/7). |

Serving stacks in use on the rig: vLLM (apodex/tools/strategist units), ik_llama.cpp (GLM-5.2 flagship
mode), embed-cpu (Qwen3-Embedding-0.6B :8005). PCIe Gen 2 ×16 makes tensor-parallel slow; pipeline
parallel (PP) across cards is what the fleet modes use.

Benchmarks to run before committing (none exist yet): DeepSeek-V4-Flash-0731 on 4 cards (vLLM PP4 INT4
vs ik_llama.cpp Q4) — tok/s single stream and at 8 concurrent agents; GLM-5.2 via KTransformers hybrid on
4 cards + CPU; LTX-2.5 int8 per-card seconds per 5 s clip; FP64 sweep throughput unaffected on 0-3.

---

## 8. CAD adapters and the "voxel CAD"

- **Fusion (Autodesk):** Windows/macOS desktop only — no native Linux client *(well known; a browser-based
  Fusion exists only as a limited preview, unverified today)*. Two local MCP routes exist on DARKNESS:
  official **Fusion MCP Server** :27182 (4 dynamic tools incl. `fusion_mcp_execute` = arbitrary Fusion API
  Python; needs Fusion desktop running and a **commercial subscription**; trial lapsed ~2026-08-01) and the
  **community Fusion360MCP add-in** :9876 (84 typed tools, newline-delimited JSON over TCP, licence-agnostic,
  used by gendesign.fusion_bridge, simlab export and formgen). Plus **fusion-data** cloud MCP (hubs/projects
  metadata, OAuth pending) and the Autodesk Product Help MCP. Fusion's own simulation/GD is **cloud-only,
  no API** (Simulation Extension A$2,636–3,101/yr AU). Fusion's embedded Python (3.14) has no pip.
  On Omarchy the options are: (a) Omarchy's Docker/KVM **Windows VM** with the add-in listening on :9876 and
  the port forwarded (software rendering; fine for API-driven export/import, poor for interactive modelling)
  *(assessment)*; (b) **dual boot** Windows ↔ ACES OS on DARKNESS; (c) a **dedicated Windows "CAD node"**
  on the LAN (Fusion + bridge) that ACES OS drives remotely; (d) VFIO GPU passthrough VM (needs a second
  GPU; DARKNESS has one).
- **Onshape (PTC):** browser-native → runs natively on Linux. **Official Onshape FeatureScript MCP Server**
  (Onshape Labs, in the Onshape App Store; blog 3 Sep 2026): an LLM writes FeatureScript straight into a
  Feature Studio, reads errors, iterates, inspects geometry. Community `onshape-mcp` (hedless; forks) wraps
  the REST API (sketching, features, assemblies, export). Onshape has a full REST API, FeatureScript, and
  App Store extensions. Nothing Onshape-specific exists in ACES yet.
- **Headless CAD already in the stack:** build123d/CadQuery (the `cad` skill: STEP/STL/3MF/GLB), ezdxf,
  trimesh, gmsh; these run natively on Linux with no licence.
- **"Voxel CAD" today** = gendesign's voxel H8 FEA + SIMP topology optimisation on voxel grids, STEP →
  voxel masks (`mesh.contains` exact voxelisation), formgen lattices, the `implicit-cad` skill (SDF/implicit
  modelling), Warp differentiable lattices. Related open libraries: OpenVDB/NanoVDB (sparse voxels), libfive
  / sdf (implicit), Manifold (mesh booleans), trimesh *(ecosystem; not yet wired)*. nTopology/Grasshopper/
  Houdini are the UI register Aaron chose for SIMLAB ("Parameter Sky").

---

## 9. Agents and models: who talks to what

- On Omarchy: Claude Code / Codex / OpenCode / Hermes / OpenClaw etc. are first-class (section 4); Hermes
  and OpenClaw are also Aaron's local-GPU agents today (OpenClaw gateway with 7 MCP servers; Hermes with
  skills/browser/kanban; both share `rag_search` over the curated KB). OpenFang (Rust agent-OS, 30 agents)
  and prime-agent (code-only agent over IPython, `rlm()` recursion) are Nabu-driven.
- ACES-specific MCP surfaces that exist: simlab-mcp :8020 (tiers, audit), rig-state :8010 (read-only),
  Fusion :27182 / :9876, Nabu APIs (evidence, systems, capabilities). Planned: `gen_topopt`,
  `gen_parametric`, `gen_nozzle`.
- Agent brief for the rig: `docs/agent-brief.yaml` (constraints, endpoints, thresholds, never/always lists).
- Model routing philosophy from Omniscient/Nabu: local-first, consented cloud burst (Claude/Fable,
  zero-retention) with a Sovereign Mode that turns burst off; policy broker mediates tool calls; approval
  spine for consequential actions; hash-chained audit ledger.

---

## 10. Commercial context and comparables (facts only; no prices invented)

- **Omniscient AI** (sister offering, locked decisions 2026-06): sovereign on-prem appliance for AU
  mid-market; 3-year operating lease (default ~A$3,900/mo in the internal cost model), subscription for
  updates/compliance; trial tier ~A$1,500/mo + ~A$6k/yr; R&DTI 43.5 % refundable offset narrative;
  customer holds key custody; no standing vendor access; signed pull-only updates with auto-rollback.
  Sales kit exists (pamphlet, 1-pager, booklet, SLA template, value calculator, finance model).
- **Noyron (LEAP 71)** = the capability benchmark (proprietary, not sold as software; LEAP 71 sells
  designs/engines). ACES differs by being on-prem, open-solver, agent-driven, evidence-native, FP64-rich.
- **Fusion Simulation Extension** A$2,636–3,101/yr (cloud solves only). **LTX-2.x** free commercial use
  under US$10 M revenue, paid above. **ArtCraft** free/open. **Omarchy** free/MIT (patron-funded).
  **Onshape** paid plans (prices not checked). nTopology, Ansys, Siemens, Cadence/MSC are the incumbent
  simulation/generative vendors (prices not checked; vendor moves recorded in the catalogs).
- Aaron's own first customers/use cases: HERMES AGV (Carbon Project), AERIS UAV family and the AERIS-X64
  radar tile (OFFICIAL:SENSITIVE — geometry must not leave the LAN), PRISM biorefinery (Power Plant
  Energy), RATO motor, EDF flying wing, container workshop, mezzanine, waterfront office, robot cell.

---

## 11. Constraints and standing rules that bind the vision

1. No fabricated data, numbers, logos or testimonials; honest empty states.
2. Open solvers are the engine; every solver enters the registry with a validation record; FP64 default.
3. Surrogates rank, the real solver signs off; every run is contemporaneous evidence (hash-chained).
4. GPU mode switching is human-only through GRID CONTROL; agents get submit tier at most; never squat on
   free VRAM; one process per GPU; declare `vram_gb`; refuse > 60 GB.
5. Never touch the rig host's driver/kernel/CUDA (cmpunlocker freeze). Containers only.
6. Licence hygiene: FluidX3D, OpenSees, non-commercial datasets excluded; track LTX revenue threshold;
   Omarchy MIT; ArtCraft MIT/Apache-2.0; GLM-5.2 and DeepSeek V4 MIT.
7. Design: GRID CONTROL stays the shell on the rig; SIMLAB is a plane inside it; Parameter Sky tokens;
   no sci-fi decoration without data; instrument, not toy.
8. Sovereignty: geometry and data stay on the LAN by default; cloud burst only by consent.
9. Images are built on the workstation and shipped; the rig has no PyPI route; SSH from DARKNESS is denied
   today (the server agent "Kimi" deploys).

---

## 12. Questions the vision must answer

1. What is ACES OS, in one sentence, to an engineering firm that has never heard of Omarchy?
2. Layering: what is "Omarchy" vs "ACES OS" vs "ACES Server" vs "ACES Cloud"? What ships as pacman
   packages, ISO, container images, model bundles? How are updates and rollbacks delivered?
3. The compute fabric: how does a desktop (DARKNESS) discover and use the server's model lanes, sim lanes
   and video lanes; how are modes exposed without violating the human-only switch rule; what happens when
   the customer has no rig (laptop-only), one GPU, or a cloud account?
4. Day one on DARKNESS: concrete install sequence, what works the first hour, what needs the Windows VM.
5. The engineering loop: CAD (Fusion/Onshape/build123d) → voxel/implicit → simulation → generative →
   manufacturing (robot JBI, G-code, DXF, SendCutSend, Bambu) → evidence → report. Which agent skills exist,
   which must be written.
6. The creative suite: ArtCraft apps + LTX video studio as first-class OS citizens — packaging, theming,
   agent control channels, where the LoRA library lives and how adapters are trained/registered/versioned.
7. Model policy: "we run the servers" vs "customer chooses any model" — concrete config surface, routing,
   sovereignty modes, how cloud models (Claude etc.) plug in via Omarchy's agent layer.
8. Product: SKUs/tiers (desktop-only, desktop + server, hosted), what is open-source vs paid, support,
   licensing stack, trial, pricing *model* (not invented numbers), the first three customers to pursue.
9. Risks and what to cut from v1; a 30/60/90-day plan with owners (Aaron, Claude Code, the server agent).

---

## 14. Addendum (read after the workflow launched, 2026-10-08)

- **Omarchy org (`omacom`) repos** relevant to a derivative: `omarchy` (branch `quattro`; carries `AGENTS.md`
  and `CLAUDE.md` for agent-first development, `plans/`, `shell/`, `themes/`, `migrations/`, `install/`),
  `omarchy-pkgs` (package build system: PKGBUILDs under `pkgbuilds/<pkg>/` with `.omarchy/package.json`
  metadata; **three channels edge → rc → stable** advanced with `bin/repo advance`; `bin/omarchy-release`;
  builds in containers, signs, syncs to the mirror; x86_64 + aarch64 under QEMU; upstream watches with
  auto-merge bump PRs; every git source pinned), `omarchy-mirror`, `omarchy-iso`, `omarchy-configurator`,
  `omarchy-chromium`, `linux` (the `linux-omarchy` kernel), `omarchy-plugin-registry` /
  `omarchy-plugin-marketplace`, `omarchy-theme-registry` / `-marketplace`, first-party apps `omacut`,
  `omawrite`, `omacalc`, `omasnap`, `herdr`, `omaterm`, `omarchy-port-forward-plugin`, `try-omarchy`,
  `try-omarchy-windows`, `omarchy-mac`, `state-of-omarchy`. **No "trademark" text anywhere in the omarchy
  repo** (gh code search) → the name's trademark status is simply unstated; a derivative should use its own
  name and say "built on Omarchy" *(assessment)*.
- **omarchy-shell IPC:** `omarchy-shell shell ping|summon <id> <json>|hide|toggle|togglePanelAt|call <id>
  <method> <arg>|rescanPlugins|reloadConfig`; plugins register their own IPC targets. Manifest `barWidget`
  carries `defaults` + a settings `schema`. Third-party plugins are **unsandboxed** (same user-level file and
  process access as the shell), land disabled after `omarchy plugin add`, show a diff on update; `--yes`
  is the path for scripts and agents. Panels/overlays/menus expose `open(payloadJson)` / `close()`.
- **LTX-2 repo packages:** `ltx-core`, `ltx-kernels`, `ltx-pipelines`, **`ltx-trainer`** (first-party LoRA /
  IC-LoRA trainer exists). docs.ltx.io: LoRA and IC-LoRA customisation for style, motion and structure;
  native EXR in/out; "more permissive licensing … clearer path to fine-tune, deploy, and commercialize";
  a prompt enhancer and duration predictor are optional separate stages. Docs have `/llms.txt` indexes.

## 15. Addendum 2 — field and device integration (Aaron, 2026-10-08, mid-session)

**New ask (verbatim intent):** "include ways for the native integration of things such as PLC integration,
robotic, and automotive systems, the CAN bus, integration for anything via SSH and decompiling tools, RF analysis
and radio modules." Read as a pillar: ACES OS must natively reach the machines engineers build and test —
industrial controllers, robot cells, vehicles, embedded devices, radios — from the same desktop and agent layer.

**Context already in Aaron's work:** the Yaskawa HC10DTP robot cell (`~\workshop-room`, formgen emits `.JBI`
jobs; ZED X cameras + AGX Orin in the rack); HERMES AGV (C5ISR vehicle, CAN and Lattice SDK integration, DGX
compute; all development-stage language only); AERIS-X64 X-band radar tile (CAN is its only command surface;
**NOT RELEASED**; plausibly DSGL-controlled — Defence Trade Controls Act 2012 s.10 gate before any supply outside
Australia); an SDR folder existed in Aaron's archives; `urdf/srdf/sdf` skills exist; electronics is a known gap
(read-only Fusion Electronics; no KiCad/SPICE wiring).

**What Arch already packages (checked 2026-10-08, archlinux.org JSON API / AUR RPC):**

| Area | Official repos (extra/core) | AUR | Not packaged (pip/venv or vendor) |
|---|---|---|---|
| RF / SDR | gnuradio 3.10.12, soapysdr 0.8.1, rtl-sdr 2.0.3, hackrf 2026.01.3, gqrx 2.17.7, urh 2.10.0 (Universal Radio Hacker: protocol analysis), sigdigger 0.3.0 | openems 0.0.36 (FDTD EM solver) | vendor SDR drivers (USRP/UHD is packaged as `libuhd` *(not checked)*) |
| Firmware and binary analysis | ghidra 12.1.2 (Apache-2.0), rizin 0.8.2, binwalk 3.1.0 (MIT), qemu-full 11.1.2 (emulation). Correction: the Arch package `cutter` 1.04 is an unrelated firewall tool, not the rizin GUI | — | the rizin GUI (Cutter) needs its own packaging |
| Automotive / CAN | python-can 4.6.1 (SocketCAN, USB-CAN adapters), python-pyserial 3.5 | python-cantools 44.2.1 (DBC decode/encode), python-udsoncan 1.26.1 (UDS diagnostics), can-utils 2025.01 (candump/cansend; also -bin and -git) | J1939 stacks (pip) |
| RF hardware drivers | libuhd 4.9.0.1 (Ettus USRP) alongside gnuradio, soapysdr, rtl-sdr, hackrf | — | — |
| PLC / industrial | — | snap7 1.4.2 + python-snap7 3.0.0 (Siemens S7), python-pyvisa 1.16.2 (instruments) | pymodbus, asyncua (OPC UA), pycomm3 (Allen-Bradley EtherNet/IP) via pip |
| Robotics | — | ros2-jazzy 2026.06.18 and ros2-humble 2026.02.20 (320 ros2-* AUR packages) | vendor controllers (Yaskawa MotoPlus/YRC1000micro tooling: JBI files already generated by formgen) |
| Device access | openssh 10.6p1, python-paramiko 5.0.0, ansible 14.5.0 | — | serial consoles via pyserial *(not checked)* |

**Implications for the plan:** (1) every tool above is native on Linux and most are a `pacman -S` away — this is
a genuine argument for an Arch-based engineering OS over Windows; (2) these are dual-use tools: the OS must frame
them as **engineering access to equipment the operator owns or is authorised to work on** (interoperability,
diagnostics, test), enforce the policy broker (device writes = consequential actions needing approval), log every
session, and carry the compliance notes (DTC Act for controlled RF/radar work; ACMA spectrum licensing for
transmitting; vehicle and machine safety standards for CAN writes and robot motion); (3) RF simulation already has a
home in the runtime plan (Palace, gprMax, openEMS) so "RF analysis" spans measurement (SDR) and simulation (FDTD/FEM)
on the same evidence ledger.

## 16. Addendum 3 — survey and photogrammetry (Aaron, 2026-10-09)

**Ask (verbatim intent):** "build this into the package for omarchy downloads": the open source photogrammetry stack
chosen for the DJI Mini 5 Pro (QGIS + FlyPath for planning, WebODM in Docker with the GPU flag for processing,
CloudCompare plus QGIS for deliverables) as an ACES OS package. Packaged as `aces-survey`; also installed on DARKNESS
(Windows) the same day.

**Facts read today (2026-10-09):**

- DJI's "Product SDK Compatibility" page (support.dji.com, customId 01700000763) lists the Mini 5 Pro, Mavic 4 Pro,
  Flip, Neo, Neo 2, Avata 360, Lito X1 and Lito 1 with Mobile SDK **No**; the current Android MSDK is 5.18.0 and its
  release notes do not name the Mini 5 Pro. Dronelink's supported-drones page stops at the Mini 4 Pro; the Litchi forum
  says the Mini 5 Pro is not compatible with Litchi or Litchi Pilot and points to DJI Fly waypoint missions; Map Pilot
  Pro depends on the DJI SDK. So on the Mini series the capture path is a DJI WPML KMZ flown by the aircraft's own
  waypoint engine, loaded by overwriting a dummy mission in DJI Fly's waypoint folder
  (`Android/data/dji.go.v5/files/waypoint/` on the RC 2 or an Android phone; the Files app on iPhone), per the
  gijamaps.com Mini 5 Pro guide and the Litchi forum how-to.
- FlyPath (plugins.qgis.org, GitHub dronnix-io/FlyPath): QGIS plugin, GPL-3.0, bundled `flypath_engine` Apache-2.0
  (NOTICE: Copyright 2026 Dronnix); 3.0.0 published 6 Oct 2026; QGIS 3.34 to 4.99; aircraft listed: Mini 3 Pro, Mini 4
  Pro, Mini 5 Pro (community-verified), Air 3, Air 3S, Mavic 3 Classic, Mavic 4 Pro, Lito X1 (field reports wanted);
  2D grid, corridor, orbit, terrain follow, cross-hatch, battery splitting; semi-automatic (interval) or full-automatic
  (waypoint per photo) capture; imports shapely and pyproj (Arch: python-shapely 2.1.2, python-pyproj 3.8.0). Zip
  sha256 `2c9f6046fb2396b978e8494de53dc58f5c88f34635332da8a4cd567acfe05579`. Other open planners seen: DroneRoute
  (MIT, 0.7.2, self-hosted, grid template, CLI push to the RC over adb, lists the Mini 4 Pro), YMapper formerly
  DJI-Mapper (MIT, desktop and Android), IPGP/waypoints_generator (Python, DJI KML).
- WebODM decoupled from the OpenDroneMap organisation on 6 April 2026 (webodm.org/blog/announcement, Piero Toffanin):
  the software is now github.com/WebODM/WebODM (AGPL-3.0) and processes with ODX (github.com/WebODM/ODX, AGPL-3.0, a
  fork of ODM with OpenSfM 1.0, GPU feature matching, RTX 50 series support, checkpoints) through the docker images
  `webodm/webodm_webapp`, `webodm/webodm_db`, `webodm/nodeodx` and `webodm/nodeodx:gpu`; latest release v3.3.0
  (5 Sep 2026, tarball sha256 `c2d72ca2c8502ecb8b08561614a3fd1fefaf3a0d39a48c03ef063bbd58284ae6`); native Windows
  and macOS installers are free from webodm.org/download. OpenDroneMap's ODM (AGPL-3.0, v3.6.2) and "OpenDroneMap
  Desktop" continue separately. `webodm.sh` selects the GPU compose file only when `GPU_NVIDIA=true`, which its
  `detect_gpus` sets on Linux through lspci or nvidia-smi and never on Windows or macOS; exporting `GPU_NVIDIA=true`
  before `start` works on every platform (verified on DARKNESS under Docker Desktop: `node-odx-1` running
  `webodm/nodeodx:gpu` sees the RTX PRO 4000 Blackwell; the console answers on port 8090).
- Arch packaging (archlinux.org JSON API and AUR RPC, 2026-10-09): qgis extra 4.2.2-1; docker extra 29.8.2-1;
  docker-compose extra 5.6.0-1; nvidia-container-toolkit extra 1.20.1-1; pciutils core 3.15.0-1; python-shapely
  extra 2.1.2-2; python-pyproj extra 3.8.0-1. AUR only: cloudcompare 2.13.2-4 and cloudcompare-git (bartus), colmap
  4.2.0-4 (bartus), meshroom 2025.1.8-1 (sfn). Not packaged anywhere: WebODM (docker), MicMac, DroneRoute, YMapper.
- CloudCompare upstream ships a portable Windows build (cloudcompare.org/release/CloudCompare_v2.13.2_bin_x64.7z)
  next to the Inno Setup installer; 2.14 is in beta. The QGIS 4 documentation still names the `QGIS3/profiles` folder
  for user profiles, so plugin installs go to `.../QGIS/QGIS3/profiles/default/python/plugins` on every platform.
- Mini 5 Pro mapping settings from the gijamaps.com guide: 50 MP, 4:3 (8192 × 6144, never 16:9), JPEG, fixed shutter
  1/1000 to 1/4000 s with auto ISO, gimbal set by the mission (-90°), interval capture; DJI Fly may reset the speed
  from the KMZ. The Mini 5 Pro can run timed shot during POI tracking (pix-pro.com blog), which gives hands-off orbits.

**What this changes in the plan:** survey and photogrammetry joins section 5b as a desktop capability (`aces-survey`).
WebODM runs on the desktop's own GPU, not on the rig, because the processing node wants the images on local disk and a
single card; the rig stays an option later through a NodeODX container registered as a second processing node. The
AGPL components run as containers and the GPL plugin lives in the user's QGIS profile; nothing links into `aces-*`
code. `server.webodm` is a new default in the aces configuration (aces-base 0.1.0-2) so `aces-console webodm` and
`aces-webodm` agree on the URL.

## 13. Sources read today

omarchy.org (home, /manual/ai/, /manual/shell-plugins/, /manual/unattended-installs/, /manual/branding/,
/manual/windows-vm/, /manual/web-apps/, /doctrine/); github.com/omacom/omarchy releases v4.0.0–4.0.4 +
LICENSE; github.com/omacom/omarchy-iso README; codetocloud.io "Omarchy 4 Quattro: What's New" (14 Aug
2026); getartcraft.com (/, /apps); github.com/storytold/photocraft README + LICENSE-MIT/APACHE;
aur.archlinux.org RPC search "craft"; huggingface.co Lightricks/LTX-2.5 (card, file list, LoRA family);
github.com/Lightricks/LTX-2 README; huggingface.co zai-org/GLM-5.2 (card, config.json);
deepseek-ai/DeepSeek-V4-Flash-0731 and DeepSeek-V4.1-Flash (cards, config.json); onshape.com blog
"How to Get Started with the Onshape FeatureScript MCP Server" (3 Sep 2026); GRID CONTROL /api/modes and
/api/status; rig-state MCP rig_services / rig_endpoints; local repo docs (ACES_HANDOFF.md, PRODUCT.md,
README.md, agent-brief.yaml) and the owner's memory notes.

Survey addendum (2026-10-09): support.dji.com Product SDK Compatibility (customId 01700000763);
developer.dji.com/doc/mobile-sdk-tutorial (MSDK 5.18.0 release notes); dronelink.com/supported-drones;
forum.flylitchi.com (Mini 5 Pro threads 21331 and 27478); support.dronesmadeeasy.com Supported Hardware;
gijamaps.com/guide/mapping-mission-mini-5-pro; plugins.qgis.org/plugins/FlyPath (3.0.0) and the zip's
metadata.txt, LICENSE, flypath_engine/LICENSE and NOTICE; github.com/dronnix-io/FlyPath; github.com/WebODM/WebODM
README, webodm.sh and docker-compose.nodeodm.gpu.nvidia.yml (master 1fcb241 and the v3.3.0 archive);
github.com/WebODM/ODX; webodm.org/blog/announcement; github.com/OpenDroneMap/ODM; github.com/fcsonline/droneroute;
github.com/YLabs-FPV/YMapper; github.com/IPGP/waypoints_generator; cloudcompare.org/release; GitHub releases API
for CloudCompare and WebODM; archlinux.org/packages JSON API and aur.archlinux.org RPC for every package named in
section 16; docs.qgis.org (profiles folder); pix-pro.com Mini 5 Pro POI blog.
