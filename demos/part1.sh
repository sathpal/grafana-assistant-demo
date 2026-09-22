#!/usr/bin/env bash
# Part 1 demo — the pieces and the pipeline: one approval travels Python -> Kafka -> Java -> Go -> Redis -> Postgres.
set -euo pipefail; source "$(dirname "$0")/common.sh"; cd "$ROOT"
head_ "Part 1 · Why an open-source AI assistant, and what it will debug"
say "Two OSS pieces: mcp-grafana (Grafana's MCP server) and goose (Block's agent). One polyglot pipeline."
require_tier; require_mcp
pause 1
head_ "The MCP server exposes Grafana as tools"
dim '$ python3 assistant/mcp_call.py --tools | head -1'; echo "tools: $(python3 assistant/mcp_call.py --tools | head -1)"
run python3 assistant/mcp_call.py user_info '{}' 200; echo
pause 1
head_ "One article, end to end"
slug="demo-part1-$(date +%H%M%S)"
approve "$slug" "Part 1 demo: one article across four languages"
for i in 1 2 3 4 5 6; do sleep 2; s=$(site "$slug"); [[ "$s" == "200" ]] && break; done
[[ "$s" == "200" ]] && ok "GET /published/$slug -> 200 (rendered by Java, image by Go, cached in Redis, stored in Postgres)" || fail "site says $s"
tid=$(docker logs cortex-api --since 2m 2>&1 | grep content_approved_published | grep "$slug" | tail -1 | python3 -c 'import sys,json;print(json.loads(sys.stdin.read().strip())["trace_id"])' 2>/dev/null || true)
if [[ -n "$tid" ]]; then
  say "Trace id from the Python API log line: $tid"
  dim "waiting 20 s for Loki and Tempo to index it..."; sleep 20
  run python3 assistant/evidence.py part3 "$slug" | sed -n '/get_tempo_trace/,$p'
  dim "Open it: $(grafana_url)/d/cortex-trace-viewer?var-trace_id=$tid"
fi
pause 1
head_ "The human view"
dim "$(grafana_url)/d/cortex-publishing-tier"
say "Next: Part 2 wires the assistant to this stack."
