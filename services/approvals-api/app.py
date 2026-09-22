"""approvals-api — the human gate of the content pipeline, reduced to what the demo needs.

POST /v1/approvals            create a pending approval (title, slug, markdown, channel)
POST /v1/approvals/{id}/approve  approve it -> produce to Kafka cortex.content.approved
The Kafka message carries the trace context in its headers (aiokafka instrumentation), so the
Java consumer's span joins this request's trace. Logs are JSON with trace_id, like the other services.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, HTTPException
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.aiokafka import AIOKafkaInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import BaseModel, Field

SERVICE = os.getenv("OTEL_SERVICE_NAME", "cortex-api")
TOPIC = "cortex.content.approved"
KAFKA = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy:4317")

resource = Resource.create({"service.name": SERVICE})
tp = TracerProvider(resource=resource); tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=ENDPOINT, insecure=True))); trace.set_tracer_provider(tp)
metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=ENDPOINT, insecure=True), export_interval_millis=15000)]))
AIOKafkaInstrumentor().instrument()
approvals_counter = metrics.get_meter(SERVICE).create_counter("cortex.approvals.decisions", description="Approval decisions")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        span = trace.get_current_span().get_span_context()
        d = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": record.levelname.lower(), "logger": record.name,
             "event": record.getMessage(), "service_name": SERVICE}
        if span.is_valid:
            d["trace_id"] = format(span.trace_id, "032x"); d["span_id"] = format(span.span_id, "016x")
        d.update(getattr(record, "fields", {}))
        return json.dumps(d)


log = logging.getLogger("approvals")
h = logging.StreamHandler(sys.stdout); h.setFormatter(JsonFormatter()); log.addHandler(h); log.setLevel("INFO")
logging.getLogger("uvicorn.access").setLevel("WARNING")


def logkv(event: str, **fields):
    log.info(event, extra={"fields": fields})


class ApprovalCreate(BaseModel):
    artifact_ref: str
    artifact_kind: str = "publish"
    summary: str | None = None
    evidence: dict = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    reviewer_id: str | None = None
    notes: str | None = None


app = FastAPI(title="cortex approvals-api")
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")
STORE: dict[str, dict] = {}
producer: AIOKafkaProducer | None = None


@app.on_event("startup")
async def _start():
    global producer
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA, linger_ms=20)
    await producer.start()
    logkv("kafka_producer_started", bootstrap=KAFKA)


@app.on_event("shutdown")
async def _stop():
    if producer:
        await producer.stop()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/v1/approvals", status_code=201)
def create(a: ApprovalCreate):
    aid = str(uuid.uuid4())
    row = {"id": aid, "state": "pending", "created_at": datetime.now(timezone.utc).isoformat(), **a.model_dump()}
    STORE[aid] = row
    logkv("approval_created", approval_id=aid, artifact_ref=a.artifact_ref)
    return row


@app.get("/v1/approvals")
def list_approvals(state: str | None = None):
    return [r for r in STORE.values() if state is None or r["state"] == state]


@app.post("/v1/approvals/{aid}/approve")
async def approve(aid: str, d: ApprovalDecision):
    row = STORE.get(aid)
    if not row:
        raise HTTPException(404, "Approval not found")
    if row["state"] != "pending":
        raise HTTPException(409, f"Approval is {row['state']}")
    row.update(state="approved", decision="approve", reviewer_id=d.reviewer_id, notes=d.notes, decided_at=datetime.now(timezone.utc).isoformat())
    approvals_counter.add(1, {"decision": "approve", "artifact_kind": row["artifact_kind"]})
    ev = row.get("evidence") or {}
    payload = {"content_id": ev.get("slug") or f"approval-{aid}", "title": ev.get("title") or "Untitled", "body_md": ev.get("markdown") or "",
               "kind": ev.get("channel") or "blog", "source": "approved", "run_id": aid, "force_media": False}
    fut = await producer.send(topic=TOPIC, value=json.dumps(payload).encode(), key=payload["content_id"].encode())
    await fut
    logkv("content_approved_published", topic=TOPIC, content_id=payload["content_id"], approval_id=aid)
    return row
