#!/usr/bin/env python3
"""Print the evidence an on-call assistant gathers for each demo part, through the Grafana MCP server.

  python3 assistant/evidence.py part3   # lag, rates, annotations, logs, the approval trace
  python3 assistant/evidence.py part4   # first-breach timeline + alert history
  python3 assistant/evidence.py part5   # consumed vs published, failures by reason, error traces
  python3 assistant/evidence.py paged   # alert-rule audit: paused? threshold vs current value? when did it last fire?
  python3 assistant/evidence.py hygiene # dashboard audit: missing metrics, histograms without buckets, short rate windows
Every line shows the tool and the query, so the audience sees exactly what the assistant would run.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROM, LOKI, TEMPO, HIST = "grafanacloud-prom", "grafanacloud-logs", "grafanacloud-traces", "grafanacloud-alert-state-history"


def mcp(tool: str, args: dict, limit: int = 200000) -> str:
    out = subprocess.run([sys.executable, str(HERE / "mcp_call.py"), tool, json.dumps(args), str(limit)], capture_output=True, text=True)
    return out.stdout.strip()


def show(tool: str, query: str, result: str) -> None:
    print(f"\n\033[36m{tool}\033[0m  {query}")
    print("   → " + result)


def prom(expr: str, window: str = "now-15m") -> list:
    raw = mcp("query_prometheus", {"datasourceUid": PROM, "expr": expr, "queryType": "instant", "startTime": window, "endTime": "now"})
    try:
        return json.loads(raw)["data"]
    except Exception:
        return []


def fmt(rows: list, keys=("topic", "source", "reason", "result", "cache", "status")) -> str:
    if not rows:
        return "(no data)"
    parts = []
    for r in rows:
        lab = ", ".join(f"{k}={r['metric'][k]}" for k in keys if k in r["metric"])
        v = r["value"][1]
        try:
            v = f"{float(v):,.3g}"
        except Exception:
            pass
        parts.append(f"{lab + ': ' if lab else ''}{v}")
    return " | ".join(parts)


def annotations(minutes: int = 30) -> None:
    now = int(time.time() * 1000)
    raw = mcp("get_annotations", {"tags": ["cortex", "change"], "limit": 12, "from": now - minutes * 60000, "to": now})
    try:
        items = sorted(json.loads(raw)["Payload"], key=lambda a: a["time"])
    except Exception:
        items = []
    show("get_annotations", 'tags ["cortex","change"]', "")
    for a in items:
        t = dt.datetime.utcfromtimestamp(a["time"] / 1000).strftime("%H:%M:%S")
        tags = [x for x in a["tags"] if x not in ("cortex", "change")]
        print(f"     {t}  {tags}  {a['text'][:100]}")


def loki(logql: str, limit: int = 6, window: str = "now-15m") -> list[tuple[str, str, str]]:
    raw = mcp("query_loki_logs", {"datasourceUid": LOKI, "logql": logql, "limit": limit, "startRfc3339": window, "endRfc3339": "now", "format": "compact"})
    rows = []
    try:
        for s in json.loads(raw).get("streams", []):
            for l in s["lines"]:
                rows.append((l["timestamp"][:10], s["labels"].get("service_name", ""), l["line"]))
    except Exception:
        pass
    return sorted(rows)


def trace_timeline(trace_id: str) -> None:
    raw = mcp("get_tempo_trace", {"datasourceUid": TEMPO, "trace_id": trace_id})
    try:
        d = json.loads(raw)["trace"]
    except Exception:
        print("   (trace not found yet)"); return
    spans = []
    for svc in d["services"]:
        for sc in svc["scopes"]:
            for s in sc["spans"]:
                st = s.get("status", {})
                spans.append((int(s["startTimeUnixNano"]), svc["serviceName"], s["name"], s.get("durationMs", 0), st.get("code", "").replace("STATUS_CODE_", ""), st.get("message", "")))
    spans.sort(); t0 = spans[0][0]
    for st, svc, name, dur, code, msg in spans:
        if "http send" in name or "http receive" in name or name in ("connect", "XADD"):
            continue
        flag = " \033[31mERROR " + msg + "\033[0m" if code == "ERROR" else ""
        print(f"     +{(st - t0) / 1e9:7.2f}s  {svc:24s} {name[:44]:44s} {dur:8.1f} ms{flag}")


def first_breach(name: str, expr: str, pred, start: str) -> tuple[str, str]:
    raw = mcp("query_prometheus", {"datasourceUid": PROM, "expr": expr, "queryType": "range", "startTime": start, "endTime": "now", "stepSeconds": 15})
    try:
        vals = json.loads(raw)["data"][0]["values"]
    except Exception:
        return name, "no data"
    for t, v in vals:
        try:
            if pred(float(v)):
                return name, f"{dt.datetime.utcfromtimestamp(t).strftime('%H:%M:%S')} (value {float(v):.3g})"
        except Exception:
            continue
    return name, "not breached"


def part3(slug: str = "cilium-on-aks") -> None:
    print("\n\033[1m== Part 3 evidence: is it the queue or the work?\033[0m")
    annotations()
    show("query_prometheus", 'sum by (topic) (kafka_consumergroup_lag{consumergroup="publisher-service"})', fmt(prom('sum by (topic) (kafka_consumergroup_lag{consumergroup="publisher-service"})')))
    show("query_prometheus", 'max_over_time(sum(kafka_consumergroup_lag{consumergroup="publisher-service"})[15m:])', fmt(prom('max_over_time(sum(kafka_consumergroup_lag{consumergroup="publisher-service"})[15m:])')))
    show("query_prometheus", "sum by (source) (rate(publisher_content_published_total[2m])) * 60", fmt(prom("sum by (source) (rate(publisher_content_published_total[2m])) * 60")))
    show("query_prometheus", 'sum by (topic) (rate(kafka_topic_partition_current_offset{topic=~"cortex.*"}[2m])) * 60', fmt(prom('sum by (topic) (rate(kafka_topic_partition_current_offset{topic=~"cortex.*"}[2m])) * 60')))
    for label, e in [("media hit ratio", 'sum(rate(media_requests_total{result="hit"}[5m])) / clamp_min(sum(rate(media_requests_total[5m])),1e-9)'),
                     ("cache-redis fill", "max(redis_memory_used_bytes) / max(redis_memory_max_bytes)"),
                     ("site read p95 (s)", "histogram_quantile(0.95, sum by (le) (rate(site_reader_latency_seconds_bucket[5m])))"),
                     ("JVM heap ratio", 'sum(jvm_memory_used_bytes{jvm_memory_type="heap"}) / sum(jvm_memory_limit_bytes{jvm_memory_type="heap"})'),
                     ("consumer members", 'max(kafka_consumergroup_members{consumergroup="publisher-service"})')]:
        show("query_prometheus", f"{label}: {e}", fmt(prom(e)))
    show("query_loki_logs", '{service_name="cortex-content-batch"} |~ "batch_config_changed|batch_started"', "")
    for t, svc, line in loki('{service_name="cortex-content-batch"} |~ "batch_config_changed|batch_started"', 3):
        j = json.loads(line); print(f"     {j['ts'][11:19]} {j['message']} items={j.get('items','')} batch_size={j.get('batch_size','')} concurrency={j.get('concurrency','')}")
    show("query_loki_logs", f'{{service_name=~"cortex-(api|publisher-service)"}} |= "{slug}"', "")
    trace_id = None
    for t, svc, line in loki(f'{{service_name=~"cortex-(api|publisher-service)"}} |= "{slug}"', 6):
        j = json.loads(line); msg = j.get("message") or j.get("event"); print(f"     {svc:26s} {msg[:90]}  trace={j.get('trace_id','')[:16]}")
        if svc == "cortex-api" and j.get("trace_id"):
            trace_id = j["trace_id"]
    if trace_id:
        show("get_tempo_trace", trace_id, "")
        trace_timeline(trace_id)


def part4(since: str = "now-12m") -> None:
    print("\n\033[1m== Part 4 evidence: what changed, in what order?\033[0m")
    annotations()
    checks = [("media renders > 100/min", 'sum(rate(media_requests_total{result="miss"}[1m]))*60', lambda v: v > 100),
              ("cache-redis memory > 90 %", "max(redis_memory_used_bytes) / max(redis_memory_max_bytes)", lambda v: v > 0.9),
              ("cache-redis evicting > 1/s", "sum(rate(redis_evicted_keys_total[1m]))", lambda v: v > 1),
              ("publisher lag > 500", 'sum(kafka_consumergroup_lag{consumergroup="publisher-service"})', lambda v: v > 500),
              ("site read p95 > 100 ms", "histogram_quantile(0.95, sum by (le) (rate(site_reader_latency_seconds_bucket[2m])))", lambda v: v > 0.1)]
    show("query_prometheus (range, 15 s steps)", f"first breach since {since}", "")
    for name, expr, pred in checks:
        n, when = first_breach(name, expr, pred, since); print(f"     {n:30s} {when}")
    show("query_loki_logs", '{service_name=~"cortex-(media-service|content-batch)"} |~ "chaos_toggle|batch_config_changed"', "")
    for t, svc, line in loki('{service_name=~"cortex-(media-service|content-batch)"} |~ "chaos_toggle|batch_config_changed"', 4):
        j = json.loads(line); print(f"     {svc:24s} {(j.get('ts') or j.get('time'))[11:19]} {j.get('message') or j.get('msg')} {j.get('scenario','')} {j.get('on','')}")
    show("query_loki_logs (alert-state-history)", '{from="state-history"} | json |= "cortex"', "")
    raw = mcp("query_loki_logs", {"datasourceUid": HIST, "logql": '{from="state-history"} | json |= "cortex"', "limit": 20, "startRfc3339": "now-30m", "endRfc3339": "now", "format": "compact"})
    try:
        rows = []
        for s in json.loads(raw).get("streams", []):
            for l in s["lines"]:
                j = json.loads(l["line"]); rows.append((int(l["timestamp"]) // 10**9, j["ruleTitle"], j["previous"], j["current"]))
        for ts, rule, prev, cur in sorted(rows):
            print(f"     {dt.datetime.utcfromtimestamp(ts).strftime('%H:%M:%S')}  {rule:30s} {prev} -> {cur}")
    except Exception:
        print("     (no alert history yet)")


def part5() -> None:
    print("\n\033[1m== Part 5 evidence: consumed minus published\033[0m")
    show("query_prometheus", 'sum by (topic) (kafka_consumergroup_lag{consumergroup="publisher-service"})', fmt(prom('sum by (topic) (kafka_consumergroup_lag{consumergroup="publisher-service"})')))
    show("query_prometheus", 'sum by (topic) (increase(kafka_consumergroup_current_offset{consumergroup="publisher-service"}[3m]))', fmt(prom('sum by (topic) (increase(kafka_consumergroup_current_offset{consumergroup="publisher-service"}[3m]))')))
    show("query_prometheus", "sum by (source) (increase(publisher_content_published_total[3m]))", fmt(prom("sum by (source) (increase(publisher_content_published_total[3m]))")))
    show("query_prometheus", "publisher_publish_failures_total", fmt(prom("publisher_publish_failures_total")))
    annotations(15)
    show("query_loki_logs", '{service_name=~"cortex-(publisher-service|api)"} |~ "publish_failed.*source=approved|content_approved_published"', "")
    for t, svc, line in loki('{service_name=~"cortex-(publisher-service|api)"} |~ "publish_failed.*source=approved|content_approved_published"', 8):
        j = json.loads(line); print(f"     {svc:26s} {(j.get('message') or j.get('event'))[:88]}  trace={j.get('trace_id','')[:16]}")
    show("query_loki_logs", 'sum by (service_name) (count_over_time({service_namespace="cortex"} | json | level=~"ERROR|error" [10m]))', mcp("query_loki_logs", {"datasourceUid": LOKI, "logql": 'sum by (service_name) (count_over_time({service_namespace="cortex"} | json | level=~"ERROR|error" [10m]))', "queryType": "instant", "startRfc3339": "now-10m", "endRfc3339": "now"}, 400)[:300])
    start = (dt.datetime.utcnow() - dt.timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ"); end = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = mcp("search_tempo_traces", {"datasourceUid": TEMPO, "query": '{ resource.service.name="cortex-publisher-service" && status=error }', "start": start, "end": end})
    show("search_tempo_traces", '{ resource.service.name="cortex-publisher-service" && status=error }', "")
    try:
        traces = json.loads(raw).get("traces", [])
    except Exception:
        traces = []
    for t in traces[:3]:
        print(f"     {t['traceID']}  {t.get('rootServiceName')} {t.get('rootTraceName')} {t.get('durationMs')} ms")
    if traces:
        show("get_tempo_trace", traces[0]["traceID"], ""); trace_timeline(traces[0]["traceID"])
    raw = mcp("alerting_manage_rules", {"operation": "list", "search_folder": "CORTEX-AKS", "limit_alerts": 1})
    show("alerting_manage_rules", "list, folder CORTEX-AKS", "")
    try:
        for r in json.loads(raw):
            print(f"     {r['title']:30s} {r['state']}")
    except Exception:
        pass


def paged(folder: str = "CORTEX-AKS") -> None:
    """Case 5: why did nobody get paged? The checks an assistant makes before blaming the on-call."""
    print(f"\n\033[1m== Alert-rule audit, folder {folder}\033[0m")
    raw = mcp("alerting_manage_rules", {"operation": "list", "search_folder": folder, "limit_alerts": 0})
    rules = json.loads(raw) if raw.startswith("[") else []
    hist = mcp("query_loki_logs", {"datasourceUid": HIST, "logql": '{from="state-history"} | json | current="Alerting"', "limit": 200,
                                   "startRfc3339": "now-24h", "endRfc3339": "now", "format": "compact"})
    last_fired: dict[str, int] = {}
    try:
        for st in json.loads(hist).get("streams", []):
            for l in st["lines"]:
                j = json.loads(l["line"]); ts = int(l["timestamp"]) // 10**9
                last_fired[j["ruleTitle"]] = max(last_fired.get(j["ruleTitle"], 0), ts)
    except Exception:
        pass
    print(f"   {'rule':30s} {'state':9s} {'paused':7s} {'for':5s} {'threshold':10s} {'current':10s} last fired (24h)")
    for r in rules:
        full = json.loads(mcp("alerting_manage_rules", {"operation": "get", "rule_uid": r["uid"]}) or "{}")
        expr, thr, op = "", None, ""
        for d in full.get("data", []):
            m = d.get("model", {})
            if m.get("expr"):
                expr = m["expr"]
            for c in m.get("conditions", []) or []:
                ev = c.get("evaluator", {}); thr = (ev.get("params") or [None])[0]; op = ev.get("type", "")
        cur = "no data"
        if expr:
            q = re.sub(r"\s*>\s*bool\s*[0-9.]+$|\s*<\s*bool\s*[0-9.]+$", "", expr)
            rows = prom(q, "now-10m")
            if rows:
                try: cur = f"{float(rows[0]['value'][1]):.3g}"
                except Exception: cur = rows[0]["value"][1]
            m2 = re.search(r"(>|<)\s*bool\s*([0-9.]+)", expr)
            if m2 and thr in (0, None):
                op, thr = ("gt" if m2.group(1) == ">" else "lt"), float(m2.group(2))
        lf = dt.datetime.utcfromtimestamp(last_fired[r["title"]]).strftime("%H:%M:%S") if r["title"] in last_fired else "never"
        flag = " \033[33mPAUSED\033[0m" if full.get("is_paused") else ""
        print(f"   {r['title']:30s} {r['state']:9s} {str(bool(full.get('is_paused'))):7s} {full.get('for', ''):5s} {op + ' ' + str(thr):10s} {cur:10s} {lf}{flag}")
    print("\n   Read: a paused rule never pages; a threshold far above the current value during an incident never pages;")
    print("   a rule that has never fired in 24h while incidents happened is watching the wrong signal.")


def hygiene(uid: str = "cortex-publishing-tier") -> None:
    """Case 7: dashboard and metric hygiene. Missing metrics, histograms without buckets, short rate windows."""
    print(f"\n\033[1m== Dashboard hygiene, {uid}\033[0m")
    raw = mcp("get_dashboard_panel_queries", {"uid": uid})
    try:
        panels = json.loads(raw)
    except Exception:
        print("   " + raw[:300]); return
    if isinstance(panels, dict):
        panels = panels.get("panels") or panels.get("queries") or []
    funcs = {"sum", "rate", "increase", "max", "min", "avg", "count", "by", "le", "or", "and", "vector", "clamp_min", "clamp_max",
             "histogram_quantile", "max_over_time", "time", "bool", "on", "ignoring", "topk", "label_replace", "abs", "without", "group"}
    seen: dict[str, bool] = {}; buckets: dict[str, int] = {}; problems = []
    for p in panels:
        title = p.get("title") or p.get("panel_title") or "?"
        for q in p.get("queries", []) if isinstance(p.get("queries"), list) else [p]:
            expr = q.get("query") or q.get("expr") or ""
            if not expr or expr.startswith("{"):
                continue
            bare = re.sub(r"\{[^}]*\}", "", expr)                       # drop label selectors
            bare = re.sub(r"\b(by|without|on|ignoring)\s*\([^)]*\)", "", bare)  # drop label lists
            for name in sorted(set(re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", bare))):
                if name in funcs or "_" not in name or name.startswith("$"):
                    continue
                if name not in seen:
                    r = mcp("list_prometheus_metric_names", {"datasourceUid": PROM, "regex": f"^{name}$", "limit": 1})
                    seen[name] = name in r
                if not seen[name]:
                    problems.append((title, name, "metric not found in Mimir", "check the name with list_prometheus_metric_names"))
                elif name.endswith("_bucket") and name not in buckets:
                    rows = prom(f"count by (le) ({name})", "now-30m"); buckets[name] = len(rows)
                    if len(rows) <= 1:
                        problems.append((title, name, f"histogram has {len(rows)} bucket(s)", "set explicit bucket boundaries; histogram_quantile is meaningless"))
            for w in re.findall(r"\[(\d+)([smh])\]", expr):
                secs = int(w[0]) * {"s": 1, "m": 60, "h": 3600}[w[1]]
                if secs < 60:
                    problems.append((title, expr[:60], f"range window {w[0]}{w[1]} < 4x scrape interval", "use [1m] or longer"))
    print(f"   panels scanned: {len(panels)}, metrics checked: {len(seen)}, histograms checked: {len(buckets)}")
    problems = list(dict.fromkeys(problems))
    if not problems:
        print("   no problems found"); return
    print(f"   {'panel':44s} {'metric / query':44s} {'problem':36s} fix")
    for t, m, pr, fx in problems:
        print(f"   {t[:43]:44s} {m[:43]:44s} {pr[:35]:36s} {fx}")


if __name__ == "__main__":
    part = sys.argv[1] if len(sys.argv) > 1 else "part3"
    {"part3": part3, "part4": part4, "part5": part5, "paged": paged, "hygiene": hygiene}[part](*sys.argv[2:])
