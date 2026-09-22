#!/usr/bin/env bash
# Part 3 demo — "publishing is stuck": batch flood, an approval that waits, the evidence, the assistant.
set -euo pipefail; source "$(dirname "$0")/common.sh"; cd "$ROOT"
head_ "Part 3 · \"Publishing is stuck\""
require_tier; require_mcp
say "Baseline: lag $(lag) messages, cache $(redis_mem)."
pause 1
head_ "The change nobody announced: the batch goes into month-end mode"
run make -s pub-chaos S=batch-flood
sleep 35
slug="cilium-on-aks-$(date +%H%M)"
head_ "An editor approves an article while the batch runs"
approve "$slug" "Cilium on AKS: eBPF dataplane in practice"
for i in 1 2 3 4; do sleep 15; printf '%s  GET /published/%s -> %s   lag %s\n' "$(date -u +%T)" "$slug" "$(site "$slug")" "$(lag)"; done
pause 1
head_ "What the assistant would gather (through the MCP server)"
run python3 assistant/evidence.py part3 "$slug"
pause 1
head_ "The assistant"
goose_run problem1.yaml
pause 1
head_ "Cleanup"
run make -s pub-chaos S=batch-flood ON=off
say "Lag drains in a few minutes; the approved article then appears. Next: Part 4, two changes, one root cause."
