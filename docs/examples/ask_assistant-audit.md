# ask_assistant example: the managed Grafana Assistant through mcp-grafana

Asked on 2026-09-22 through `make mcp-assistant` (port 8320) against the techstronautk8s Grafana Cloud stack. Wall time 65 s.

**Prompt**

> You are helping an on-call engineer with the CORTEX publishing tier. Answer with evidence: (1) Which alert rules in folder CORTEX-AKS are currently paused? (2) What is the Kafka consumer lag for consumer group publisher-service right now, by topic (metric kafka_consumergroup_lag)? (3) In the last 30 minutes, how many publish failures does publisher_publish_failures_total show, by reason? Quote the exact queries you ran, keep it under 250 words.

**Reply** (verbatim)

No published data exists for that metric in this window; likely no failures recorded (or the metric isn't currently emitting samples).

**1. Paused alert rules — folder CORTEX-AKS (uid efyjo97jj1o8wf)**
All 6 rules are currently paused (`paused=true`):
- PublisherConsumerLagGrowing
- PublishFailuresHigh
- MediaCacheHitRatioLow
- CacheRedisEvicting
- SiteReadLatencyHigh
- PublishedContentGap

Query used (rules.get on each rule_uid) shows `paused=true` for all.

**2. Kafka consumer lag — group publisher-service**
Query: `sum by (topic) (kafka_consumergroup_lag{consumergroup="publisher-service"})` (datasource grafanacloud-prom, instant)
Result:
- cortex.content.approved: -2 (negative value, likely offset reset/rebalance artifact rather than real backlog)
- cortex.content.reindex: 0

No backlog currently; lag is effectively zero.

**3. Publish failures — last 30 min**
Queries tried:
- `sum by (reason) (increase(publisher_publish_failures_total[30m]))` → no data
- `publisher_publish_failures_total` (instant and 30m range) → no data

No samples for `publisher_publish_failures_total` were returned for the last 30 minutes on grafanacloud-prom, meaning either zero failures occurred (counter not incremented/exposed) or the series is absent. Cannot report a per-reason breakdown from this data; recommend confirming the metric is being scraped from publisher-service if failures are expected.

`contextId` for a follow-up turn: `fa0b322f-c39e-4483-97f0-b700f97cc70e`

All three answers were correct at the time: the six rules had been paused an hour earlier, lag was zero, and the failure counter's series did not exist because no publish had failed since the publisher last started.
