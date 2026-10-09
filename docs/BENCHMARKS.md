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
