"""Shared bootstrap for the content-batch, site-reader and seed processes.

OTLP (gRPC) traces + metrics to Alloy; JSON logs with trace_id/span_id on stdout
(Alloy tails the container and ships them to Loki with service_name).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import httpx
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import Histogram, MeterProvider
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

PUBLISHER_URL = os.getenv("PUBLISHER_URL", "http://publisher-service:8085")
MEDIA_URL = os.getenv("MEDIA_URL", "http://media-service:8086")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
POSTGRES_DSN = os.getenv(
    "BATCH_POSTGRES_DSN",
    f"postgresql://{os.getenv('POSTGRES_USER', 'cortex')}:{os.getenv('POSTGRES_PASSWORD', 'cortex_dev_only')}@postgres:5432/{os.getenv('POSTGRES_DB', 'cortex')}",
)
GRAFANA_URL = os.getenv("GRAFANA_URL", "").rstrip("/")
GRAFANA_SA_TOKEN = os.getenv("GRAFANA_SA_TOKEN", "")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        span = trace.get_current_span().get_span_context()
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service_name": os.getenv("OTEL_SERVICE_NAME", "cortex-content-batch"),
        }
        if span.is_valid:
            payload["trace_id"] = format(span.trace_id, "032x")
            payload["span_id"] = format(span.span_id, "016x")
        for k, v in getattr(record, "extra_fields", {}).items():
            payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def get_logger(name: str) -> logging.Logger:
    root = logging.getLogger()
    if not root.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(JsonFormatter())
        root.addHandler(h)
        root.setLevel(os.getenv("LOG_LEVEL", "INFO"))
        logging.getLogger("httpx").setLevel("WARNING")
        logging.getLogger("uvicorn.access").setLevel("WARNING")
    return logging.getLogger(name)


def log_kv(logger: logging.Logger, level: int, message: str, **fields):
    logger.log(level, message, extra={"extra_fields": fields})


def setup_otel(service_name: str) -> None:
    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)
    resource = Resource.create({"service.name": service_name})
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy:4317")
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(tp)
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint, insecure=True), export_interval_millis=15_000)
    seconds = View(instrument_type=Histogram,
                   aggregation=ExplicitBucketHistogramAggregation([0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600]))
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader], views=[seconds]))


def grafana_annotation(text: str, tags: list[str], time_ms: int | None = None, time_end_ms: int | None = None) -> None:
    """Write a change/batch annotation to Grafana Cloud so dashboards and the assistant can see it."""
    if not GRAFANA_URL or not GRAFANA_SA_TOKEN:
        return
    body = {"time": time_ms or int(time.time() * 1000), "tags": tags, "text": text}
    if time_end_ms:
        body["timeEnd"] = time_end_ms
    try:
        httpx.post(f"{GRAFANA_URL}/api/annotations", json=body, headers={"Authorization": f"Bearer {GRAFANA_SA_TOKEN}"}, timeout=5)
    except Exception:  # never let telemetry break the batch
        pass
