#!/usr/bin/env bash
# Part 6 demo — guard rails: a read-only server refuses writes, fewer tools, a budget, and the audit trail.
set -euo pipefail; source "$(dirname "$0")/common.sh"; cd "$ROOT"
head_ "Part 6 · Guard rails"
BIN="${MCP_GRAFANA_BIN:-$(command -v mcp-grafana || echo "$HOME/go/bin/mcp-grafana")}"
[[ -x "$BIN" ]] || { fail "mcp-grafana binary not found (set MCP_GRAFANA_BIN)"; exit 1; }
GRAFANA_URL_="$(grafana_url)"; TOKEN="$(grep -E '^GRAFANA_SA_TOKEN=' "$ROOT/.env" | cut -d= -f2- | tr -d '\r')"
lsof -ti :8301 -ti :8302 2>/dev/null | xargs kill 2>/dev/null || true
head_ "1. Read-only by default: --disable-write"
GRAFANA_URL="$GRAFANA_URL_" GRAFANA_SERVICE_ACCOUNT_TOKEN="$TOKEN" "$BIN" -t streamable-http -address localhost:8301 --disable-write >/tmp/mcp-ro.log 2>&1 &
RO=$!; sleep 3
MCP_URL=http://localhost:8301/mcp python3 assistant/mcp_call.py --tools | awk 'NR==1{print "tools with --disable-write:", $0}'
echo "write tools present: $(MCP_URL=http://localhost:8301/mcp python3 assistant/mcp_call.py --tools | grep -c -E '^(create_annotation|update_dashboard|create_incident)$' || true)" 
kill $RO 2>/dev/null || true; sleep 1
pause 1
head_ "2. Fewer tools: disable what this team never needs"
GRAFANA_URL="$GRAFANA_URL_" GRAFANA_SERVICE_ACCOUNT_TOKEN="$TOKEN" "$BIN" -t streamable-http -address localhost:8302 --disable-oncall --disable-incident --disable-admin --disable-sift --disable-pyroscope --disable-cloudwatch --disable-elasticsearch --disable-agento11y --disable-asserts --disable-docs >/tmp/mcp-min.log 2>&1 &
MIN=$!; sleep 2
MCP_URL=http://localhost:8302/mcp python3 assistant/mcp_call.py --tools | awk 'NR==1{print "tools after --disable-*:", $0}'
kill $MIN 2>/dev/null || true
pause 1
head_ "3. The audit trail is the server log"
dim "every Grafana API call the assistant makes is one line here (org, endpoint, status):"
tail -5 /tmp/mcp-min.log | cut -c1-160
pause 1
head_ "4. Budget and repeatability"
dim "  goose run --recipe problem1.yaml --max-tool-repetitions 5     # stop a looping investigation"
dim "  GOOSE_MODEL=claude-opus-5 (pinned)  ·  recipes in git  ·  transcripts in demos/runs/"
head_ "5. What made the difference"
say "change annotations · one service_name everywhere · trace context through Kafka · six house rules · the exporters you already run"
say "That's the series. Everything is in this repo; the articles live in github.com/sathpal/grafana-articles."
