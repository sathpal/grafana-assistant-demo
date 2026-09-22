# assistant

The open-source assistant: [goose](https://github.com/block/goose) + [mcp-grafana](https://github.com/grafana/mcp-grafana).

- `recipes/*.yaml` — one goose recipe per investigation (`smoke`, `problem1`, `problem2`, `problem3`). Each carries the
  six house rules as `instructions`, the prompt, and the `grafana` extension pointing at `http://localhost:8300/mcp`.
  Run: `goose run --recipe assistant/recipes/problem1.yaml`.
- `mcp_call.py` — call any tool on the MCP server from a shell: `python3 assistant/mcp_call.py --tools`,
  `python3 assistant/mcp_call.py query_prometheus '{"datasourceUid":"grafanacloud-prom","expr":"up","queryType":"instant","startTime":"now-5m","endTime":"now"}'`.
- `evidence.py` — prints, tool by tool, the evidence an on-call assistant gathers for each incident
  (`part3`, `part4`, `part5`), so an audience sees exactly what the model would run.

House rules (the part that makes a general agent useful on call):

```text
1. Start with user_info and list_datasources so you know what you can reach.
2. Never assert a cause without a query result that shows it. Quote the PromQL, LogQL or TraceQL you ran.
3. Walk symptom to cause: business metric -> service RED metrics -> dependency metrics -> log lines -> a trace.
4. Prefer the last 15 minutes; widen only if the signal is absent. Read the cortex+change annotations first.
5. Read-only unless the prompt says "write". Writes: annotations, alert rules, dashboard panels only.
6. Finish with: root cause, evidence per signal (with a deep link), blast radius, fix, confidence.
```
