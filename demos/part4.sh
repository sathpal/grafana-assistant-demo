#!/usr/bin/env bash
# Part 4 demo — "what changed?": no-TTL config change, then a force-render batch; timeline from annotations + alert history.
set -euo pipefail; source "$(dirname "$0")/common.sh"; cd "$ROOT"
head_ "Part 4 · \"What changed?\""
require_tier; require_mcp
wait_lag 20
start_iso="$(date -u -v-1M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "1 minute ago" +%Y-%m-%dT%H:%M:%SZ)"
head_ "Change 1 (quiet): media-service caches images without a TTL"
run make -s pub-chaos S=no-ttl
say "Waiting two minutes so the two changes are distinguishable on the timeline..."; sleep 120
head_ "Change 2 (loud): full reindex with forced image re-renders"
run make -s pub-chaos S=batch-flood-media
for i in 1 2 3 4 5 6; do sleep 20; printf '%s  redis %s  evicted %s  lag %s\n' "$(date -u +%T)" "$(redis_mem)" "$(redis_evicted)" "$(lag)"; done
pause 1
head_ "What the assistant would gather"
run python3 assistant/evidence.py part4 "$start_iso"
pause 1
head_ "The assistant"
goose_run problem2.yaml
pause 1
head_ "Cleanup"
run make -s pub-chaos S=reset; docker exec cortex-cache-redis redis-cli flushall >/dev/null
say "Next: Part 5, the failure with no symptoms."
