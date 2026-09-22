#!/usr/bin/env bash
# Part 2 demo — setup: token, MCP server, goose, house rules, smoke test.
set -euo pipefail; source "$(dirname "$0")/common.sh"; cd "$ROOT"
head_ "Part 2 · Setup in 15 minutes"
say "Step 1 — service account token: Administration > Users and access > Service accounts (Viewer to read, Editor to write)."
say "Step 2 — the MCP server:"
dim '  GRAFANA_URL=https://<stack>.grafana.net GRAFANA_SERVICE_ACCOUNT_TOKEN=<token> mcp-grafana -t streamable-http -address localhost:8300'
require_mcp
dim '$ python3 assistant/mcp_call.py --tools'; python3 assistant/mcp_call.py --tools | awk 'NR==1{print "tools:", $0} NR>1 && NR<=13{printf "  %s\n", $0} NR==14{print "  ..."}'
pause 1
say "Step 3 — goose:"
command -v goose >/dev/null && ok "goose $(goose --version 2>/dev/null | head -1)" || dim "  brew install block-goose-cli"
dim '  goose session --with-streamable-http-extension http://localhost:8300/mcp'
say "Step 4 — house rules (six lines, in every recipe):"
sed -n '/^instructions:/,/^prompt:/p' assistant/recipes/smoke.yaml | sed '1d;$d' | sed 's/^  //' | head -12
pause 1
say "Step 5 — the smoke test (what the assistant should do first: user_info, then list_datasources):"
run python3 assistant/mcp_call.py user_info '{}' 300; echo
dim '$ python3 assistant/mcp_call.py list_datasources {}'
python3 assistant/mcp_call.py list_datasources '{}' 20000 | python3 -c '
import sys, json
d = json.loads(sys.stdin.read())
items = d if isinstance(d, list) else next((v for v in d.values() if isinstance(v, list)), [])
for x in items[:6]:
    print("  %-44s %-12s %s" % (x.get("name"), x.get("type"), x.get("uid")))' || true
goose_run smoke.yaml
say "Next: Part 3 breaks publishing on purpose."
