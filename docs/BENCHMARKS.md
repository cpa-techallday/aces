# ACES lane benchmarks

Appended by `aces lane bench`; every row is also a `lane.test` ledger entry.

## 2026-10-09T00:32:45+00:00  server 192.168.1.65  mode sim-lab-4

Manifest generated 2026-10-09T00:32:42+00:00. Probe: list models, one 32-token completion at temperature 0, one request with a tool definition. Tool calling counts only when the server returns a structured tool call.

| Lane | Model | Endpoint | Models list | Completion | Reply | Tool call |
|---|---|---|---|---|---|---|
| model/glm-flash | QuantTrio/GLM-4.7-Flash-AWQ | http://192.168.1.65:8016/v1 | 0.169 s | 0.55 s, 58.1 tok/s | 1.  **Analyze the user's request:** The  | rejected: http 400 ({"error":{"message":"\"auto\" tool choice requires --enable-auto-tool-choice and --tool-ca) |
| model/glm-flash-2 | QuantTrio/GLM-4.7-Flash-AWQ | http://192.168.1.65:8017/v1 | 0.203 s | 0.371 s, 86.2 tok/s | 1.  **Analyze the user's request:** The  | rejected: http 400 ({"error":{"message":"\"auto\" tool choice requires --enable-auto-tool-choice and --tool-ca) |
| model/gpt-oss | openai/gpt-oss-20b | http://192.168.1.65:8018/v1 | 0.213 s | 0.228 s, 135.8 tok/s | OK | no structured call (answered in text, 0.21 s) |

