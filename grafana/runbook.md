# Runbook — the publishing tier (grafana-assistant-demo)

Path: Python API (approval) → Kafka `cortex.content.approved` → Java `publisher-service` → Go `media-service` (+ `cache-redis`) → Postgres `published_content`.
Batch: Python `content-batch` → Kafka `cortex.content.reindex` → the same consumer. Readers: `site-reader` → `GET /published/{id}` (Redis, then Postgres).

Dashboard: **CORTEX-AKS — Publishing tier**. Change events are `cortex` + `change` annotations (chaos toggles, batch runs, config changes).

| Alert | Meaning | First checks | Known causes (chaos name) |
|---|---|---|---|
| PublisherConsumerLagGrowing | approved content is queuing in Kafka | lag by topic; published/min; publish p95; media p95 | batch flood (`batch-flood`), paused consumer (`consumer-pause`), slow render (`slow-render`, `media-slow-render`) |
| PublishFailuresHigh | consumer acks but content never lands | failures by reason; Loki `publish_failed` lines; trace with `status=error` | Postgres write rejected (`db-write-fail`), media-service down |
| MediaCacheHitRatioLow | every publish re-renders an image | hit/miss; cache-redis evictions + memory; TTL in `/chaos` | TTL 1 s (`cache-stampede`), keys without TTL (`no-ttl`), batch `force_media` |
| CacheRedisEvicting | the 48 MiB cache tier is full | memory used vs max; evicted/s; keys without TTL | `no-ttl`, batch flood with `force_media` |
| SiteReadLatencyHigh | readers wait > 1 s | JVM heap/GC; Hikari pool; cache hit ratio | `heap-pressure`, cache misses → Postgres |

Toggle scenarios: `make chaos S=<name> [ON=off]`; `make pub-chaos S=reset` clears everything.
