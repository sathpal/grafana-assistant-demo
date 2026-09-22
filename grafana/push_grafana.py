#!/usr/bin/env python3
"""Push the Publishing-tier dashboard + alert rules to Grafana Cloud (dashboards-as-code).

Reads GRAFANA_URL / GRAFANA_SA_TOKEN from .env. Idempotent: dashboards are overwritten by uid,
alert rules are upserted by uid. Run: make pub-grafana
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
URL = os.environ["GRAFANA_URL"].rstrip("/")
TOKEN = os.environ["GRAFANA_SA_TOKEN"]
PROM = "grafanacloud-prom"
LOKI = "grafanacloud-logs"
TEMPO = "grafanacloud-traces"
DASH_UID = "cortex-publishing-tier"
FOLDER_TITLE = "CORTEX-AKS"


def api(method: str, path: str, body=None, headers=None):
    req = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def folder_uid() -> str:
    _, folders = api("GET", "/api/folders?limit=200")
    for f in folders:
        if f["title"] == FOLDER_TITLE:
            return f["uid"]
    st, f = api("POST", "/api/folders", {"title": FOLDER_TITLE})
    return f["uid"]


# ---------------------------------------------------------------- dashboard
_pid = 0
def pid():
    global _pid
    _pid += 1
    return _pid

def ds(uid=PROM, typ="prometheus"):
    return {"type": typ, "uid": uid}

def target(expr, legend="", ref="A", instant=False):
    return {"datasource": ds(), "expr": expr, "legendFormat": legend or "__auto", "refId": ref, "instant": instant, "range": not instant}

def panel(title, typ, targets, x, y, w, h, unit=None, thresholds=None, extra=None, description=None):
    p = {"id": pid(), "title": title, "type": typ, "datasource": ds(), "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "targets": targets, "fieldConfig": {"defaults": {}, "overrides": []}, "options": {}}
    if description:
        p["description"] = description
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    if thresholds:
        p["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": thresholds}
        if typ == "stat":
            p["options"]["colorMode"] = "background"
    if typ == "timeseries":
        p["fieldConfig"]["defaults"]["custom"] = {"lineWidth": 2, "fillOpacity": 12, "showPoints": "never"}
        p["options"] = {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi", "sort": "desc"}}
    if typ == "stat":
        p["options"].update({"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "graphMode": "area", "textMode": "value"})
    if extra:
        p.update(extra)
    return p

def row(title, y):
    return {"id": pid(), "type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}

def text(md, x, y, w, h):
    return {"id": pid(), "type": "text", "gridPos": {"x": x, "y": y, "w": w, "h": h}, "options": {"mode": "markdown", "content": md}}

GREEN_RED = [{"color": "green", "value": None}, {"color": "red", "value": 0.5}]

def build_dashboard():
    P = []
    y = 0
    P.append(text(
        "### CORTEX-AKS · Publishing tier — Kafka · Java · Go · Python batch · Redis\n"
        "Approved content flows **Python API → Kafka `cortex.content.approved` → Java publisher-service → Go media-service (Redis cache) → Postgres**; "
        "a **Python batch** re-indexes published content through `cortex.content.reindex`; **site-reader** traffic reads the Java path. "
        "Orange markers = change events (`cortex` + `change` annotations: chaos toggles, batch runs). Every panel keeps your time range; "
        "click a series → Explore.", 0, y, 24, 3))
    y += 3
    P.append(row("Is publishing OK? — symptom first", y)); y += 1
    P += [
        panel("Published / min", "stat", [target("sum(rate(publisher_content_published_total[5m])) * 60")], 0, y, 4, 5, unit="short"),
        panel("Publish failures (15 m)", "stat", [target("sum(increase(publisher_publish_failures_total[15m])) or vector(0)")], 4, y, 4, 5, unit="short",
              thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]),
        panel("Consumer lag · publisher-service", "stat", [target('sum(kafka_consumergroup_lag{consumergroup="publisher-service"}) or vector(0)')], 8, y, 4, 5, unit="short",
              thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 200}, {"color": "red", "value": 500}]),
        panel("Site read p95", "stat", [target("histogram_quantile(0.95, sum by (le) (rate(site_reader_latency_seconds_bucket[5m])))")], 12, y, 4, 5, unit="s",
              thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.5}, {"color": "red", "value": 1}]),
        panel("Media cache hit ratio", "stat", [target('sum(rate(media_requests_total{result="hit"}[5m])) / clamp_min(sum(rate(media_requests_total[5m])), 1e-9)')], 16, y, 4, 5, unit="percentunit",
              thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 0.5}, {"color": "green", "value": 0.8}]),
        panel("Last batch run (age)", "stat", [target("time() - max(cortex_batch_last_run_timestamp_seconds)")], 20, y, 4, 5, unit="s",
              thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 900}]),
    ]
    y += 5
    P.append(row("Kafka → Java publisher-service (Spring Boot, OTel Java agent)", y)); y += 1
    P += [
        panel("Consumer lag by topic / partition", "timeseries", [target('sum by (topic, partition) (kafka_consumergroup_lag{consumergroup="publisher-service"})', "{{topic}} p{{partition}}")], 0, y, 8, 8, unit="short"),
        panel("Published / min by source", "timeseries", [target("sum by (source) (rate(publisher_content_published_total[2m])) * 60", "{{source}}")], 8, y, 8, 8, unit="short"),
        panel("Publish duration p95 by source", "timeseries", [target("histogram_quantile(0.95, sum by (le, source) (rate(publisher_publish_duration_milliseconds_bucket[5m])))", "{{source}}")], 16, y, 8, 8, unit="ms"),
    ]
    y += 8
    P += [
        panel("Publish failures / min by reason", "timeseries", [target("sum by (reason) (rate(publisher_publish_failures_total[2m])) * 60", "{{reason}}")], 0, y, 8, 7, unit="short"),
        panel("Messages in / min by topic (Kafka)", "timeseries", [target('sum by (topic) (rate(kafka_topic_partition_current_offset{topic=~"cortex.*"}[2m])) * 60', "{{topic}}")], 8, y, 8, 7, unit="short"),
        panel("Postgres connection pool (Hikari) — active vs max", "timeseries", [target('sum(hikaricp_connections_active)', "active"), target('sum(hikaricp_connections_max)', "max", "B")], 16, y, 8, 7, unit="short"),
    ]
    y += 7
    P.append(row("Go media-service + cache Redis", y)); y += 1
    P += [
        panel("Media requests / min — hit vs miss", "timeseries", [target("sum by (result) (rate(media_requests_total[2m])) * 60", "{{result}}")], 0, y, 8, 7, unit="short"),
        panel("Render p95 (cache miss)", "timeseries", [target("histogram_quantile(0.95, sum by (le) (rate(media_render_duration_seconds_bucket[5m])))", "p95")], 8, y, 8, 7, unit="s"),
        panel("Goroutines (leak shows here)", "timeseries", [target('max(media_goroutines)', "goroutines"), target("sum(media_leaked_goroutines)", "leaked (chaos)", "B")], 16, y, 8, 7, unit="short"),
    ]
    y += 7
    P += [
        panel("cache-redis memory used vs max", "timeseries", [target("max(redis_memory_used_bytes)", "used"), target("max(redis_memory_max_bytes)", "max", "B")], 0, y, 8, 7, unit="bytes"),
        panel("cache-redis evicted keys / s + hit ratio", "timeseries", [target("rate(redis_evicted_keys_total[2m])", "evicted/s"), target("rate(redis_keyspace_hits_total[2m]) / clamp_min(rate(redis_keyspace_hits_total[2m]) + rate(redis_keyspace_misses_total[2m]), 1e-9)", "hit ratio", "B")], 8, y, 8, 7, unit="short"),
        panel("cache-redis connected clients", "timeseries", [target("max(redis_connected_clients)", "clients")], 16, y, 8, 7, unit="short"),
    ]
    y += 7
    P.append(row("JVM — publisher-service", y)); y += 1
    P += [
        panel("Heap used vs max", "timeseries", [target('sum(jvm_memory_used_bytes{jvm_memory_type="heap", service_name="cortex-publisher-service"})', "used"), target('sum(jvm_memory_limit_bytes{jvm_memory_type="heap", service_name="cortex-publisher-service"})', "max", "B")], 0, y, 8, 7, unit="bytes"),
        panel("GC pause p95", "timeseries", [target('histogram_quantile(0.95, sum by (le, jvm_gc_name) (rate(jvm_gc_duration_seconds_bucket{service_name="cortex-publisher-service"}[5m])))', "{{jvm_gc_name}}")], 8, y, 8, 7, unit="s"),
        panel("CPU (recent utilisation) + threads", "timeseries", [target('avg(jvm_cpu_recent_utilization_ratio{service_name="cortex-publisher-service"})', "cpu"), target('sum(jvm_thread_count{service_name="cortex-publisher-service"}) / 100', "threads / 100", "B")], 16, y, 8, 7, unit="percentunit"),
    ]
    y += 7
    P.append(row("Python content-batch (reindex job)", y)); y += 1
    P += [
        panel("Batch runs / 15 m by status", "timeseries", [target("sum by (status, trigger) (increase(cortex_batch_runs_total[15m]))", "{{status}} · {{trigger}}")], 0, y, 8, 7, unit="short", extra={"fieldConfig": {"defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 60}}, "overrides": []}}),
        panel("Batch duration p95 / items per run", "timeseries", [target("histogram_quantile(0.95, sum by (le) (rate(cortex_batch_duration_seconds_bucket[30m])))", "duration p95 (s)"), target("max(cortex_batch_last_run_items)", "items (last run)", "B")], 8, y, 8, 7, unit="short"),
        panel("Reindex messages produced / min", "timeseries", [target("sum(rate(cortex_batch_items_total[2m])) * 60", "items/min"), target("max(cortex_batch_in_progress) * 100", "in progress ×100", "B")], 16, y, 8, 7, unit="short"),
    ]
    y += 7
    P.append(row("Site read path (Python reader → Java → Redis / Postgres)", y)); y += 1
    P += [
        panel("Reads / min by status · cache", "timeseries", [target("sum by (status, cache) (rate(site_reader_requests_total[2m])) * 60", "{{status}} · {{cache}}")], 0, y, 12, 7, unit="short"),
        panel("Read latency p50 / p95 / p99", "timeseries", [target("histogram_quantile(0.5, sum by (le) (rate(site_reader_latency_seconds_bucket[5m])))", "p50"), target("histogram_quantile(0.95, sum by (le) (rate(site_reader_latency_seconds_bucket[5m])))", "p95", "B"), target("histogram_quantile(0.99, sum by (le) (rate(site_reader_latency_seconds_bucket[5m])))", "p99", "C")], 12, y, 12, 7, unit="s"),
    ]
    y += 7
    P.append(row("Evidence — logs and traces across four languages", y)); y += 1
    P.append({"id": pid(), "type": "logs", "title": "Errors + chaos toggles across the tier (Loki)", "datasource": ds(LOKI, "loki"),
              "gridPos": {"x": 0, "y": y, "w": 12, "h": 10},
              "targets": [{"datasource": ds(LOKI, "loki"), "refId": "A",
                           "expr": '{service_name=~"cortex-(publisher-service|media-service|content-batch|site-reader|kafka|cache-redis)"} |~ "(?i)(error|warn|chaos|batch_(started|finished|config))"'}],
              "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True, "dedupStrategy": "none", "sortOrder": "Descending"}})
    P.append({"id": pid(), "type": "table", "title": "Failing or slow publishes (Tempo) — click a trace id", "datasource": ds(TEMPO, "tempo"),
              "gridPos": {"x": 12, "y": y, "w": 12, "h": 10},
              "targets": [{"datasource": ds(TEMPO, "tempo"), "refId": "A", "queryType": "traceql", "limit": 20, "tableType": "traces",
                           "query": '{ resource.service.name="cortex-publisher-service" && (status=error || duration > 1s) }'}],
              "options": {}})
    return {
        "uid": DASH_UID,
        "title": "CORTEX-AKS — Publishing tier (Kafka · Java · Go · batch · Redis)",
        "tags": ["grafana-assistant-demo", "publishing", "kafka", "java", "go", "batch", "redis"],
        "timezone": "browser",
        "refresh": "30s",
        "time": {"from": "now-30m", "to": "now"},
        "schemaVersion": 39,
        "editable": True,
        "annotations": {"list": [
            {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
            {"datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "iconColor": "orange", "name": "Changes (chaos · batch · config)",
             "target": {"type": "tags", "tags": ["cortex", "change"], "matchAny": False, "limit": 200}},
        ]},
        "panels": P,
    }


# ---------------------------------------------------------------- alert rules
def rule(uid, title, expr, for_, summary, description, severity, labels=None):
    return {
        "uid": uid, "title": title, "ruleGroup": "cortex-publishing-tier", "folderUID": FOLDER,
        "for": for_, "noDataState": "OK", "execErrState": "Error", "condition": "C", "orgID": 1,
        "labels": {"severity": severity, "tier": "publishing", "team": "content-platform", **(labels or {})},
        "annotations": {"summary": summary, "description": description,
                        "dashboard_url": f"{URL}/d/{DASH_UID}",
                        "runbook_url": "https://github.com/sathpal/grafana-assistant-demo/blob/main/grafana/runbook.md"},
        "data": [
            {"refId": "A", "datasourceUid": PROM, "relativeTimeRange": {"from": 600, "to": 0},
             "model": {"refId": "A", "expr": expr, "instant": True, "range": False, "intervalMs": 1000, "maxDataPoints": 43200}},
            {"refId": "C", "datasourceUid": "__expr__", "relativeTimeRange": {"from": 0, "to": 0},
             "model": {"refId": "C", "type": "threshold", "datasource": {"type": "__expr__", "uid": "__expr__"}, "expression": "A",
                       "conditions": [{"evaluator": {"params": [0], "type": "gt"}}]}},
        ],
    }

RULES = [
    ("cortex-pub-lag", "PublisherConsumerLagGrowing",
     'sum(kafka_consumergroup_lag{consumergroup="publisher-service"}) > bool 500', "2m",
     "Kafka consumer lag for publisher-service > 500 for 2m: approved content is not reaching the site",
     "Lag {{ $value }} messages. Causes seen: batch flood (content-batch reindex), paused/slow consumer, slow media-service. Check the Publishing tier dashboard, row 2.", "critical"),
    ("cortex-pub-fail", "PublishFailuresHigh",
     'sum(rate(publisher_publish_failures_total[5m])) > bool 0.2', "2m",
     "publisher-service is failing publishes (> 0.2/s for 5m)",
     "Failures by reason: {{ $labels.reason }}. db_write = Postgres rejected the write (content acked on Kafka but never visible), media = media-service unreachable.", "critical"),
    ("cortex-media-hit", "MediaCacheHitRatioLow",
     'sum(rate(media_requests_total{result="hit"}[5m])) / clamp_min(sum(rate(media_requests_total[5m])), 1e-9) < bool 0.5', "3m",
     "media-service cache hit ratio < 50% for 3m: every publish is re-rendering images",
     "Check cache-redis evictions/memory and the media-service TTL (chaos: cache-stampede / no-ttl).", "warning"),
    ("cortex-redis-evict", "CacheRedisEvicting",
     'sum(rate(redis_evicted_keys_total[5m])) > bool 1', "2m",
     "cache-redis is evicting keys (> 1/s): the 48 MiB cache tier is full",
     "Keys without TTL or a batch re-render flood fill the LRU cache; hit ratio and site latency follow.", "warning"),
    ("cortex-site-p95", "SiteReadLatencyHigh",
     'histogram_quantile(0.95, sum by (le) (rate(site_reader_latency_seconds_bucket[5m]))) > bool 1', "2m",
     "Site read p95 > 1 s for 2m (Java read path)",
     "Reader p95 {{ $value }}s. Walk: publisher-service JVM (heap/GC) → cache-redis → Postgres.", "warning"),
]


def push_rules():
    _, existing = api("GET", "/api/v1/provisioning/alert-rules")
    have = {r["uid"]: r for r in existing} if isinstance(existing, list) else {}
    for uid, title, expr, for_, summary, desc, sev in RULES:
        body = rule(uid, title, expr, for_, summary, desc, sev)
        if uid in have:
            body["isPaused"] = have[uid].get("isPaused", False)  # re-pushing never silently un-pauses a rule
            st, r = api("PUT", f"/api/v1/provisioning/alert-rules/{uid}", body, {"X-Disable-Provenance": "true"})
        else:
            st, r = api("POST", "/api/v1/provisioning/alert-rules", body, {"X-Disable-Provenance": "true"})
        print(f"  rule {title}: {st}" + ("" if st in (200, 201) else f" {r}"))
    st, r = api("PUT", f"/api/v1/provisioning/folder/{FOLDER}/rule-groups/cortex-publishing-tier",
                {"interval": "1m", "rules": []}, {"X-Disable-Provenance": "true"}) if False else (0, None)


if __name__ == "__main__":
    FOLDER = folder_uid()
    dash = build_dashboard()
    out = ROOT / "grafana" / "dashboards" / f"{DASH_UID}.json"
    out.write_text(json.dumps(dash, indent=2))
    st, r = api("POST", "/api/dashboards/db", {"dashboard": dash, "folderUid": FOLDER, "overwrite": True, "message": "publishing tier dashboards-as-code"})
    print(f"dashboard {DASH_UID}: {st} {r.get('url') if isinstance(r, dict) else r}")
    if "--no-alerts" not in sys.argv:
        push_rules()
