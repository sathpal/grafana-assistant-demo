#!/usr/bin/env bash
# Shared helpers for the six demo scripts. Source me.
# DEMO_AUTO=1 skips the "press Enter" pauses (CI / recording).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# GRAFANA_TARGET=oss -> the self-hosted Grafana OSS stack (make oss-up, make mcp-oss on :8310)
if [[ "${GRAFANA_TARGET:-cloud}" == "oss" ]]; then MCP_URL="${MCP_URL:-http://localhost:8310/mcp}"; else MCP_URL="${MCP_URL:-http://localhost:8300/mcp}"; fi
export GRAFANA_TARGET
export MCP_URL
c_head=$'\033[1;33m'; c_say=$'\033[1;36m'; c_dim=$'\033[2m'; c_ok=$'\033[32m'; c_err=$'\033[31m'; c_off=$'\033[0m'

head_()  { printf '\n%s== %s ==%s\n' "$c_head" "$*" "$c_off"; }
say()    { printf '%s%s%s\n' "$c_say" "$*" "$c_off"; }
dim()    { printf '%s%s%s\n' "$c_dim" "$*" "$c_off"; }
run()    { printf '%s$ %s%s\n' "$c_dim" "$*" "$c_off"; "$@"; }
pause()  { [[ "${DEMO_AUTO:-0}" == "1" ]] && { sleep "${1:-1}"; return; }; read -r -p $'\n'"${c_dim}[Enter to continue]${c_off} "; }
ok()     { printf '%s✓ %s%s\n' "$c_ok" "$*" "$c_off"; }
fail()   { printf '%s✗ %s%s\n' "$c_err" "$*" "$c_off"; }

need() { command -v "$1" >/dev/null 2>&1 || { fail "missing: $1"; exit 1; }; }

mcp_up() {  # is the Grafana MCP server answering?
  curl -s -m 5 "$MCP_URL" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"demo","version":"0"}}}' | grep -q '"serverInfo"'
}
require_mcp() {
  mcp_up || { fail "Grafana MCP server not answering at $MCP_URL"; dim "start it:  GRAFANA_URL=... GRAFANA_SERVICE_ACCOUNT_TOKEN=... mcp-grafana -t streamable-http -address localhost:8300"; exit 1; }
  ok "mcp-grafana answering at $MCP_URL"
}
require_tier() {
  for u in "localhost:8085/actuator/health publisher-service" "localhost:8086/healthz media-service" "localhost:8087/healthz content-batch" "localhost:8000/healthz cortex-api"; do
    set -- $u; curl -fsS -m 3 "$1" >/dev/null 2>&1 && ok "$2 up" || { fail "$2 not up ($1) — run: make pub-up"; exit 1; }
  done
}
grafana_url() { if [[ "${GRAFANA_TARGET:-cloud}" == "oss" ]]; then echo "${GRAFANA_OSS_URL:-http://localhost:3001}"; else grep -E '^GRAFANA_URL=' "$ROOT/.env" | cut -d= -f2- | tr -d '\r'; fi; }

lag()       { docker exec cortex-kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group publisher-service 2>/dev/null | awk '/cortex/ {s+=$6} END {print s+0}'; }
redis_mem() { docker exec cortex-cache-redis redis-cli info memory | grep used_memory_human | cut -d: -f2 | tr -d '\r'; }
redis_evicted() { docker exec cortex-cache-redis redis-cli info stats | grep '^evicted_keys' | cut -d: -f2 | tr -d '\r'; }
wait_lag()  {  # wait until the publisher has caught up (max ~6 min)
  local n=0; while [ "$(lag)" -gt "${1:-20}" ] && [ $n -lt 36 ]; do printf '\r%swaiting for lag to drain: %s   %s' "$c_dim" "$(lag)" "$c_off"; sleep 10; n=$((n+1)); done; printf '\r'; ok "lag $(lag)"
}
site()      { curl -s -o /dev/null -w '%{http_code}' "localhost:8085/published/$1"; }

approve() {  # approve <slug> "<title>"  -> creates + approves an article through the Python API (Kafka hook fires)
  python3 - "$1" "$2" <<'PY'
import json, sys, time, urllib.request
slug, title = sys.argv[1], sys.argv[2]
def call(m, p, b=None):
    req = urllib.request.Request("http://localhost:8000" + p, method=m, data=json.dumps(b).encode() if b else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read())
a = call("POST", "/v1/approvals", {"artifact_ref": f"blog/{slug}", "artifact_kind": "publish", "summary": title,
      "evidence": {"channel": "hashnode", "title": title, "slug": slug, "tags": ["aks"],
                   "markdown": f"# {title}\n\nApproved by the editor at {time.strftime('%H:%M:%S UTC', time.gmtime())}."}})
r = call("POST", f"/v1/approvals/{a['id']}/approve", {"reviewer_id": "editor-priya", "notes": "publish now"})
print(f"approved {slug} -> {r['state']}")
PY
}

goose_run() {  # goose_run <recipe>: runs the recipe if goose + a provider key are present, else prints the command
  local recipe="$ROOT/assistant/recipes/$1"
  if command -v goose >/dev/null 2>&1 && [[ -n "${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}${GOOGLE_API_KEY:-}${OLLAMA_HOST:-}" ]]; then
    say "Running the assistant: goose run --recipe $1"
    goose run --recipe "$recipe" ${DEMO_GOOSE_ARGS:-} | tee "$ROOT/demos/runs/$(basename "$1" .yaml).txt"
  else
    dim "goose not configured (need goose + a provider key in the environment). The assistant would run:"
    dim "  goose run --recipe assistant/recipes/$1"
  fi
}
mkdir -p "$ROOT/demos/runs"
