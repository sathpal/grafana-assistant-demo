#!/usr/bin/env bash
# Toggle a publishing-tier failure scenario and record it as a Grafana change annotation.
#   scripts/chaos.sh <scenario> [on|off]
# Scenarios:
#   publisher (Java):  slow-render · db-write-fail · heap-pressure · consumer-pause
#   media (Go):        media-slow-render · cache-stampede · no-ttl · goroutine-leak
#   batch (Python):    batch-flood (3000 reindex msgs, concurrency 32) · batch-flood-media (same + force_media: re-renders every image)
#   all off:           reset
set -euo pipefail
S="${1:?scenario}"; ON="${2:-on}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GRAFANA_URL="$(grep -E "^GRAFANA_URL=" "$ROOT/.env" | cut -d= -f2- | tr -d "\r")"
GRAFANA_SA_TOKEN="$(grep -E "^GRAFANA_SA_TOKEN=" "$ROOT/.env" | cut -d= -f2- | tr -d "\r")"
flag=true; [[ "$ON" == "off" || "$ON" == "false" ]] && flag=false

annotate() {  # $1 kind  $2 component  $3 text
  [[ -z "${GRAFANA_URL:-}" || -z "${GRAFANA_SA_TOKEN:-}" ]] && return 0
  curl -s -o /dev/null -X POST "$GRAFANA_URL/api/annotations" \
    -H "Authorization: Bearer $GRAFANA_SA_TOKEN" -H "Content-Type: application/json" \
    -d "{\"time\":$(( $(date +%s) * 1000 )),\"tags\":[\"cortex\",\"change\",\"kind:$1\",\"component:$2\",\"scenario:$S\",\"state:$ON\"],\"text\":\"$3\"}"
}

case "$S" in
  slow-render|db-write-fail|heap-pressure|consumer-pause)
    curl -s -X POST "localhost:8085/chaos/$S?on=$flag"; echo
    annotate config cortex-publisher-service "config: publisher-service $S=$ON (chaos toggle)";;
  media-slow-render|cache-stampede|no-ttl|goroutine-leak)
    curl -s -X POST "localhost:8086/chaos?scenario=$S&on=$flag"; echo
    annotate config cortex-media-service "config: media-service $S=$ON (chaos toggle)";;
  batch-flood|batch-flood-media)
    if $flag; then
      fm=false; [[ "$S" == "batch-flood-media" ]] && fm=true
      curl -s -X POST localhost:8087/config -H 'Content-Type: application/json' -d "{\"batch_size\":3000,\"concurrency\":32,\"force_media\":$fm}"; echo
      annotate batch cortex-content-batch "batch: month-end full reindex enabled (batch_size 25 -> 3000, concurrency 4 -> 32, force_media $fm)"
      curl -s -X POST localhost:8087/run >/dev/null &
    else
      curl -s -X POST localhost:8087/config -H 'Content-Type: application/json' -d '{"reset":true}'; echo
      annotate batch cortex-content-batch "batch: reindex config reset to defaults (batch_size 25, concurrency 4)"
    fi;;
  reset)
    for s in slow-render db-write-fail heap-pressure consumer-pause; do curl -s -X POST "localhost:8085/chaos/$s?on=false" >/dev/null; done
    for s in media-slow-render cache-stampede no-ttl goroutine-leak; do curl -s -X POST "localhost:8086/chaos?scenario=$s&on=false" >/dev/null; done
    curl -s -X POST localhost:8087/config -H 'Content-Type: application/json' -d '{"reset":true}' >/dev/null
    annotate config publishing-tier "config: all chaos scenarios reset"
    echo "all scenarios off";;
  *) echo "unknown scenario $S"; exit 1;;
esac
