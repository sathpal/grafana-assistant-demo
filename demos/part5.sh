#!/usr/bin/env bash
# Part 5 demo — the silent failure: Kafka acks, Postgres rejects, site 404s; then the assistant writes back.
set -euo pipefail; source "$(dirname "$0")/common.sh"; cd "$ROOT"
head_ "Part 5 · The silent failure"
require_tier; require_mcp
wait_lag 20
head_ "The change: Postgres writes are rejected, but the consumer still commits its offset"
run make -s pub-chaos S=db-write-fail
sleep 10
head_ "Editors approve three articles"
stamp=$(date +%H%M)
for s in "keda-scaling-aks:KEDA on AKS: event-driven autoscaling" "workload-identity-aks:Workload Identity on AKS" "argocd-aks-gitops:Argo CD on AKS: GitOps without the drama"; do
  approve "${s%%:*}-$stamp" "${s#*:}"; sleep 3
done
sleep 20
for s in keda-scaling-aks workload-identity-aks argocd-aks-gitops; do printf '  GET /published/%s-%s -> %s\n' "$s" "$stamp" "$(site "$s-$stamp")"; done
say "Lag: $(lag)  (nothing waiting — that is the trap)"
pause 1
dim "giving Tempo a moment to index the failing traces..."; sleep 45
head_ "What the assistant would gather"
run python3 assistant/evidence.py part5
pause 1
head_ "The assistant (reads, then three writes: annotation, alert rule, panel — needs an Editor token)"
goose_run problem3.yaml
if ! command -v goose >/dev/null 2>&1 || [[ -z "${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}${GOOGLE_API_KEY:-}${OLLAMA_HOST:-}" ]]; then
  dim "Without a model, the same three writes as raw MCP calls:"; run bash demos/p3_writes.sh | cut -c1-160
fi
pause 1
head_ "Cleanup"
run make -s pub-chaos S=db-write-fail ON=off
say "Re-approve the three articles now and they publish. Next: Part 6, guard rails."
