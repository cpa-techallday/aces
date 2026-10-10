# ACES lane benchmarks

Appended by `aces lane bench`; every row is also a `lane.test` ledger entry.

## 2026-10-09T00:32:45+00:00  server 192.168.1.65  mode sim-lab-4

Manifest generated 2026-10-09T00:32:42+00:00. Probe: list models, one 32-token completion at temperature 0, one request with a tool definition. Tool calling counts only when the server returns a structured tool call.

| Lane | Model | Endpoint | Models list | Completion | Reply | Tool call |
|---|---|---|---|---|---|---|
| model/glm-flash | QuantTrio/GLM-4.7-Flash-AWQ | http://192.168.1.65:8016/v1 | 0.169 s | 0.55 s, 58.1 tok/s | 1.  **Analyze the user's request:** The  | rejected: http 400 ({"error":{"message":"\"auto\" tool choice requires --enable-auto-tool-choice and --tool-ca) |
| model/glm-flash-2 | QuantTrio/GLM-4.7-Flash-AWQ | http://192.168.1.65:8017/v1 | 0.203 s | 0.371 s, 86.2 tok/s | 1.  **Analyze the user's request:** The  | rejected: http 400 ({"error":{"message":"\"auto\" tool choice requires --enable-auto-tool-choice and --tool-ca) |
| model/gpt-oss | openai/gpt-oss-20b | http://192.168.1.65:8018/v1 | 0.213 s | 0.228 s, 135.8 tok/s | OK | no structured call (answered in text, 0.21 s) |

## Golden path wall time

Rows are appended by `aces golden show RUN --bench-out docs/BENCHMARKS.md` from real runs only.

## 2026-10-09T13:02:39.346+00:00  aces golden root-mount on Darkness

| Machine | CPU | Devices used | Workers | Designs ok/n | gen/fea/check mm | Sweep s | Loop s | Check s | Total s | Outcome | Grade | Run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Darkness | Intel(R) Core(TM) Ultra 9 285HX (24 logical) | cpu/numpy x10, NVIDIA RTX PRO 4000 Blackwell SFF Edition (cupy, warp) | 10 | 9/10 | 4/4/2 | 14.4 | 7.3 | 7.1 | 45.1 | pass | verified-desktop | 20261009T130156Z-b3ac99 |

## 2026-10-09T14:31:14.107+00:00  aces golden root-mount on Darkness

| Machine | CPU | Devices used | Workers | Designs ok/n | gen/fea/check mm | Sweep s | Loop s | Check s | Total s | Outcome | Grade | Run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Darkness | Intel(R) Core(TM) Ultra 9 285HX (24 logical) | cpu/numpy x10, NVIDIA RTX PRO 4000 Blackwell SFF Edition (cupy, warp) | 10 | 9/10 | 4/4/2 | 13.5 | 6.9 | 6.8 | 42.8 | pass | verified-desktop | 20261009T143033Z-b3ac99 |

## 2026-10-09T14:40:30.267+00:00  aces golden root-mount on aces-rehearsal

Rehearsal VM: real Omarchy 4.0.4 on Hyper-V, 4 vCPUs of the same host CPU, no GPU, packaged venv (Python 3.14.7) from
the signed LAN repo. Row written on the VM by `aces golden show 20261009T144006Z-b3ac99 --bench-out` and copied here.

| Machine | CPU | Devices used | Workers | Designs ok/n | gen/fea/check mm | Sweep s | Loop s | Check s | Total s | Outcome | Grade | Run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| aces-rehearsal | Intel(R) Core(TM) Ultra 9 285HX (4 logical) | cpu/numpy x4 | 4 | 9/10 | 4/4/2 | 7.7 | 3.4 | 5.9 | 23.7 | pass | verified-desktop | 20261009T144006Z-b3ac99 |

## Golden path with the rig lane (2026-10-10, acceptance of the rig integration)

Rows written by `aces golden show <run> --bench-out` on the machine that ran each run and copied here unchanged. The rig is the ACES Server (8x NVIDIA CMP 170HX, mode sim-lab-4, pool GPUs 0 to 3) reached through simlab-mcp 0.4.0 at `http://192.168.1.65:8022/mcp`; the rig operator's hand-run containers held GPUs 1 to 3 outside the queue during these runs, so the rig cases ran on the free GPUs with lease waits (27.4 s for the first check case; up to 239 s for one sweep case). Check s includes the rig's conforming check (tet10 at 2 mm, CalculiX ccx 2.23 on 16 rig CPU threads, about 54 s). The "Devices used" text of a run that used more than one rig GPU is mis-joined by `report.py` (Known gaps in PACKAGES.md); the records are right.

## 2026-10-10T08:41:20.853+00:00  aces golden root-mount on Darkness

| Machine | CPU | Devices used | Workers | Designs ok/n | gen/fea/check mm | Sweep s | Loop s | Check s | Total s | Outcome | Grade | Run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Darkness | Intel(R) Core(TM) Ultra 9 285HX (24 logical) | cpu/numpy x10, NVIDIA RTX PRO 4000 Blackwell SFF Edition (cupy, warp), rig: NVIDIA CMP 170HX x1 (cupy, mg), rig cpu (ccx x16) | 10 | 9/10 | 4/4/2 | 15.7 | 8.1 | 65.0 | 109.8 | pass | rig-verified | 20261010T083934Z-b3ac99 |

## 2026-10-10T08:48:20.351+00:00  aces golden root-mount on Darkness

| Machine | CPU | Devices used | Workers | Designs ok/n | gen/fea/check mm | Sweep s | Loop s | Check s | Total s | Outcome | Grade | Run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Darkness | Intel(R) Core(TM) Ultra 9 285HX (24 logical) | cpu/numpy, NVIDIA RTX PRO 4000 Blackwell SFF Edition (cupy, warp), rig: NVIDIA CMP 170HX), rig gpu 2 (NVIDIA CMP 170HX x1 (cupy), rig: NVIDIA CMP 170HX x2 (cupy, mg), rig cpu (ccx x16) | 1 | 9/10 | 4/4/2 | 247.4 | 10.0 | 64.7 | 340.2 | pass | rig-verified | 20261010T084243Z-b3ac99 |

## 2026-10-10T08:42:23.972+00:00  aces golden root-mount on aces-rehearsal

| Machine | CPU | Devices used | Workers | Designs ok/n | gen/fea/check mm | Sweep s | Loop s | Check s | Total s | Outcome | Grade | Run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| aces-rehearsal | Intel(R) Core(TM) Ultra 9 285HX (4 logical) | cpu/numpy x4, cpu x4 | 4 | 9/10 | 4/4/2 | 8.1 | 3.4 | 36.3 | 55.0 | pass | verified-desktop | 20261010T084129Z-b3ac99 |

## 2026-10-10T08:50:43.042+00:00  aces golden root-mount on aces-rehearsal

| Machine | CPU | Devices used | Workers | Designs ok/n | gen/fea/check mm | Sweep s | Loop s | Check s | Total s | Outcome | Grade | Run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| aces-rehearsal | Intel(R) Core(TM) Ultra 9 285HX (4 logical) | cpu/numpy x4, rig: NVIDIA CMP 170HX x2 (cupy, mg), rig cpu (ccx x16) | 4 | 9/10 | 4/4/2 | 8.1 | 3.2 | 65.9 | 83.8 | pass | rig-verified | 20261010T084920Z-b3ac99 |

The --rig-sweep row above as aces-engineering 0.1.0-5 renders the same run (`aces golden show 20261010T084243Z-b3ac99 --bench-out`, 2026-10-10 09:30 UTC): the Devices used cell now names the three rig GPUs the records hold.

| Machine | CPU | Devices used | Workers | Designs ok/n | gen/fea/check mm | Sweep s | Loop s | Check s | Total s | Outcome | Grade | Run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Darkness | Intel(R) Core(TM) Ultra 9 285HX (24 logical) | cpu/numpy, NVIDIA RTX PRO 4000 Blackwell SFF Edition (cupy, warp), rig: NVIDIA CMP 170HX x3 (cupy, mg), rig cpu (ccx x16) | 1 | 9/10 | 4/4/2 | 247.4 | 10.0 | 64.7 | 340.2 | pass | rig-verified | 20261010T084243Z-b3ac99 |

## 2026-10-10T10:22:59.738+00:00  aces golden root-mount on aces-rehearsal

The domain of this run came from aces-cad, not the built-in demo: `disc_domain.py` run and pulled on the VM (bundle `cad/local/20261010T102209Z-disc`, `part.step` sha256 `2a88ca85e746…`), then `aces golden run --spec /tmp/rm.json --step <bundle>/part.step --project cad-gold --new` (acceptance step 12 of the aces-cad section in PACKAGES.md). Desktop only, CalculiX on the VM; `aces golden verify` passes 11 checks with the rig and resolve rows INFO (not run).

| Machine | CPU | Devices used | Workers | Designs ok/n | gen/fea/check mm | Sweep s | Loop s | Check s | Total s | Outcome | Grade | Run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| aces-rehearsal | Intel(R) Core(TM) Ultra 9 285HX (4 logical) | cpu/numpy x4, cpu x4 | 4 | 9/10 | 4/4/2 | 7.3 | 3.1 | 33.4 | 49.5 | pass | verified-desktop | 20261010T102210Z-bc7391 |
