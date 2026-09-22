"""site-reader — synthetic readers of the published site (RED traffic for the Java read path)."""
from __future__ import annotations

import logging
import os
import random
import time

import httpx
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

import common

common.setup_otel("cortex-site-reader")
HTTPXClientInstrumentor().instrument()
log = common.get_logger("site-reader")
tracer = trace.get_tracer("cortex-site-reader")
meter = metrics.get_meter("cortex-site-reader")
req_counter = meter.create_counter("site.reader.requests", description="Reader requests by status and cache result")
latency = meter.create_histogram("site.reader.latency", unit="s")

RPS = float(os.getenv("READER_RPS", "5"))
client = httpx.Client(base_url=common.PUBLISHER_URL, timeout=5)
ids: list[str] = []
last_refresh = 0.0


def refresh_ids():
    global ids, last_refresh
    try:
        ids = client.get("/published/ids", params={"limit": 1000}).json()
        last_refresh = time.time()
        common.log_kv(log, logging.INFO, "ids_refreshed", count=len(ids))
    except Exception as e:  # noqa: BLE001
        common.log_kv(log, logging.WARNING, "ids_refresh_failed", error=str(e))


while True:
    if time.time() - last_refresh > 60 or not ids:
        refresh_ids()
        if not ids:
            time.sleep(5)
            continue
    cid = random.choice(ids)
    t0 = time.perf_counter()
    with tracer.start_as_current_span("site.read", attributes={"content.id": cid}) as span:
        status, cache = "error", "none"
        try:
            r = client.get(f"/published/{cid}")
            status = str(r.status_code)
            if r.status_code == 200:
                cache = r.json().get("cache", "none")
            span.set_attribute("http.response.status_code", r.status_code)
            span.set_attribute("media.cache", cache)
            if r.status_code >= 500 or (time.perf_counter() - t0) > 1.0:
                common.log_kv(log, logging.WARNING, "slow_or_failed_read", content_id=cid, status=status, ms=round((time.perf_counter() - t0) * 1000))
        except Exception as e:  # noqa: BLE001
            span.record_exception(e)
            common.log_kv(log, logging.ERROR, "read_failed", content_id=cid, error=str(e))
    took = time.perf_counter() - t0
    req_counter.add(1, {"status": status, "cache": cache})
    latency.record(took, {"status": status})
    time.sleep(max(0.0, 1.0 / RPS - took))
