# Demos

One narrated, scripted demo per part of the series. `make demo P=<n>` from the repo root; `DEMO_AUTO=1` skips the
"press Enter" pauses for recordings.

| part | script | what the audience sees | length |
|---|---|---|---|
| 1 | `part1.sh` | tool count, `user_info`, one approval travelling Python → Kafka → Java → Go → Redis → Postgres, its trace | 1 min |
| 2 | `part2.sh` | MCP tools, goose, the six house rules, the smoke test (and the recipe if a model key is present) | 1 min |
| 3 | `part3.sh` | batch flood, an approval that 404s while lag drains, the nine-call evidence, the assistant, cleanup | 4 min |
| 4 | `part4.sh` | no-TTL change, two minutes, force-render batch, Redis and lag samples, first-breach timeline + alert history, cleanup | 6 min |
| 5 | `part5.sh` | Postgres write failure, three approvals that 404 with zero lag, consumed-vs-published evidence, the three writes, cleanup | 3 min |
| 6 | `part6.sh` | `--disable-write` removes the write tools, `--disable-*` trims 81 → 55, the server log as audit trail | 1 min |

Prerequisites: `make up && make seed && make grafana`, the MCP server on `localhost:8300` (`make mcp`), and for Part 6 the
`mcp-grafana` binary on the PATH (or `MCP_GRAFANA_BIN`). With `goose` and a provider key in the environment, Parts 2 to 5
also run the matching recipe from `assistant/recipes/` and save the transcript under `demos/runs/`; without one they print
the command and show the same evidence through `assistant/evidence.py`.

`p3_writes.sh` is the raw-MCP version of the assistant's three writes in Part 5 (annotation, alert rule, panel); it is
idempotent.

All six demos and both audits also run against the self-hosted Grafana OSS stack: `make oss-up && make mcp-oss &`, then
`GRAFANA_TARGET=oss make demo P=<n>` (the MCP URL switches to :8310 and links point at http://localhost:3001).
