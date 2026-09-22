"""content-batch — the scheduled "reindex" job of the publishing tier.

Every BATCH_INTERVAL_S it selects up to BATCH_SIZE published articles from Postgres,
re-publishes each one through Kafka (cortex.content.reindex → Java publisher-service)
and, when force_media is on, asks the Go media-service to re-render the OG image.

One trace per run (content.reindex.batch) with a child span per chunk; producer spans
carry the trace context in Kafka headers so the JVM consumer joins the same trace.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import psycopg
from confluent_kafka import Producer
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from opentelemetry import context as otel_context, metrics, trace
from opentelemetry.instrumentation.confluent_kafka import ConfluentKafkaInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from pydantic import BaseModel

import common

common.setup_otel("cortex-content-batch")
PsycopgInstrumentor().instrument()
HTTPXClientInstrumentor().instrument()
log = common.get_logger("content-batch")
tracer = trace.get_tracer("cortex-content-batch")
meter = metrics.get_meter("cortex-content-batch")

runs_counter = meter.create_counter("cortex.batch.runs", description="Batch runs by status")
items_counter = meter.create_counter("cortex.batch.items", description="Items processed by batch runs")
duration_hist = meter.create_histogram("cortex.batch.duration", unit="s", description="Batch run duration")
in_progress = meter.create_up_down_counter("cortex.batch.in_progress", description="Batch runs currently executing")
last_run_gauge_value = {"ts": 0.0, "items": 0, "duration": 0.0}
meter.create_observable_gauge("cortex.batch.last_run_timestamp", callbacks=[lambda opts: [metrics.Observation(last_run_gauge_value["ts"])]], unit="s")
meter.create_observable_gauge("cortex.batch.last_run_items", callbacks=[lambda opts: [metrics.Observation(last_run_gauge_value["items"])]])

DEFAULTS = {
    "batch_size": int(os.getenv("BATCH_SIZE", "25")),
    "concurrency": int(os.getenv("BATCH_CONCURRENCY", "4")),
    "interval_s": int(os.getenv("BATCH_INTERVAL_S", "300")),
    "force_media": os.getenv("BATCH_FORCE_MEDIA", "false").lower() == "true",
}
config = dict(DEFAULTS)
state = {"running": False, "last_run": None, "runs": 0}
_lock = threading.Lock()

_instr = ConfluentKafkaInstrumentor()
producer = _instr.instrument_producer(Producer({"bootstrap.servers": common.KAFKA_BOOTSTRAP, "linger.ms": 20, "batch.num.messages": 500}))


def select_ids(limit: int) -> list[tuple[str, str]]:
    with psycopg.connect(common.POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("select content_id, title from published_content order by random() limit %s", (limit,))
            return [(r[0], r[1]) for r in cur.fetchall()]


CHANNELS = ["web", "amp", "rss", "email", "medium", "devto", "hashnode", "linkedin", "newsletter", "print"]


def process_item(ctx, run_id: str, item: tuple[str, str, str], force_media: bool) -> None:
    token = otel_context.attach(ctx)
    try:
        content_id, title, channel = item
        payload = {
            "content_id": content_id,
            "title": title,
            "body_md": f"# {title}\n\nReindexed by batch {run_id} at {datetime.now(timezone.utc).isoformat()}.\n\n" + "Lorem ipsum dolor sit amet. " * 40,
            "kind": channel,
            "source": "reindex",
            "run_id": run_id,
            "force_media": force_media,
        }
        producer.produce("cortex.content.reindex", key=content_id, value=json.dumps(payload).encode())
        producer.poll(0)
        if force_media:
            httpx.post(f"{common.MEDIA_URL}/media/og-image", json={"content_id": content_id, "title": title, "variant": channel, "force": True}, timeout=10)
    finally:
        otel_context.detach(token)


def run_batch(trigger: str) -> dict:
    with _lock:
        if state["running"]:
            return {"status": "skipped", "reason": "already running"}
        state["running"] = True
    run_id = uuid.uuid4().hex[:8]
    cfg = dict(config)
    t0 = time.time()
    in_progress.add(1)
    status = "ok"
    processed = 0
    with tracer.start_as_current_span("content.reindex.batch") as span:
        span.set_attribute("batch.run_id", run_id)
        span.set_attribute("batch.trigger", trigger)
        span.set_attribute("batch.size", cfg["batch_size"])
        span.set_attribute("batch.concurrency", cfg["concurrency"])
        span.set_attribute("batch.force_media", cfg["force_media"])
        common.grafana_annotation(
            f"batch: content reindex run {run_id} started (size={cfg['batch_size']}, concurrency={cfg['concurrency']}, force_media={cfg['force_media']}, trigger={trigger})",
            ["cortex", "change", "kind:batch", "component:content-batch", f"run:{run_id}"], int(t0 * 1000))
        try:
            rows = select_ids(cfg["batch_size"])
            # A full reindex republishes every article for every delivery channel.
            items = [(cid, title, ch) for ch in CHANNELS for (cid, title) in rows][: cfg["batch_size"]]
            span.set_attribute("batch.rows", len(rows))
            span.set_attribute("batch.items", len(items))
            common.log_kv(log, logging.INFO, "batch_started", run_id=run_id, trigger=trigger, items=len(items), **{k: v for k, v in cfg.items() if k != "interval_s"})
            ctx = otel_context.get_current()
            chunk = 50
            with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as pool:
                for i in range(0, len(items), chunk):
                    with tracer.start_as_current_span("reindex.chunk") as cs:
                        part = items[i:i + chunk]
                        cs.set_attribute("batch.chunk.index", i // chunk)
                        cs.set_attribute("batch.chunk.size", len(part))
                        cctx = otel_context.get_current()
                        list(pool.map(lambda it: process_item(cctx, run_id, it, cfg["force_media"]), part))
                        processed += len(part)
                        items_counter.add(len(part), {"status": "ok"})
            producer.flush(30)
        except Exception as e:  # noqa: BLE001
            status = "error"
            span.record_exception(e)
            span.set_status(trace.StatusCode.ERROR, str(e))
            common.log_kv(log, logging.ERROR, "batch_failed", run_id=run_id, error=str(e))
        finally:
            took = time.time() - t0
            in_progress.add(-1)
            runs_counter.add(1, {"status": status, "trigger": trigger})
            duration_hist.record(took, {"status": status})
            last_run_gauge_value.update({"ts": time.time(), "items": processed, "duration": took})
            state.update({"running": False, "runs": state["runs"] + 1,
                          "last_run": {"run_id": run_id, "status": status, "items": processed, "duration_s": round(took, 2), "finished_at": datetime.now(timezone.utc).isoformat()}})
            common.grafana_annotation(
                f"batch: content reindex run {run_id} {status} — {processed} items in {took:.1f}s",
                ["cortex", "change", "kind:batch", "component:content-batch", f"run:{run_id}", f"status:{status}"], int(t0 * 1000), int(time.time() * 1000))
            common.log_kv(log, logging.INFO, "batch_finished", run_id=run_id, status=status, items=processed, duration_s=round(took, 2))
    return state["last_run"]


app = FastAPI(title="cortex-content-batch")
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")


class Config(BaseModel):
    batch_size: int | None = None
    concurrency: int | None = None
    interval_s: int | None = None
    force_media: bool | None = None
    reset: bool | None = None


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/config")
def get_config():
    return {"config": config, "state": state}


@app.post("/config")
def set_config(c: Config):
    if c.reset:
        config.update(DEFAULTS)
    for k, v in c.model_dump(exclude_none=True).items():
        if k in config:
            config[k] = v
    common.log_kv(log, logging.WARNING, "batch_config_changed", **config)
    return {"config": config}


@app.post("/run")
async def run_now():
    return await run_in_threadpool(run_batch, "manual")


async def scheduler():
    await asyncio.sleep(20)
    while True:
        try:
            await run_in_threadpool(run_batch, "scheduled")
        except Exception as e:  # noqa: BLE001
            common.log_kv(log, logging.ERROR, "scheduler_error", error=str(e))
        await asyncio.sleep(max(15, config["interval_s"]) + random.uniform(0, 5))


@app.on_event("startup")
async def _start():
    asyncio.create_task(scheduler())
