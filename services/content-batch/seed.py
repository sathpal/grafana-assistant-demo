"""Seed the publishing tier with synthetic approved articles (direct /publish, same code path as Kafka)."""
from __future__ import annotations

import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor

import httpx

import common

log = common.get_logger("seed")
N = int(os.getenv("SEED_COUNT", "300"))
TOPICS = ["AKS Automatic", "OpenTelemetry Collector", "Grafana Alloy", "KEDA", "Workload Identity", "Argo CD", "Karpenter on AKS", "Cilium", "Kyverno", "Tempo TraceQL"]


def article(i: int) -> dict:
    t = random.choice(TOPICS)
    return {
        "content_id": f"art-{i:04d}",
        "title": f"{t}: field notes #{i}",
        "body_md": f"# {t}\n\n" + "\n\n".join(f"Paragraph {p}: " + f"Grounded, source-verified prose about {t}. " * 12 for p in range(1, 6)),
        "kind": "blog",
        "source": "seed",
    }


def post(i: int):
    r = httpx.post(f"{common.PUBLISHER_URL}/publish", json=article(i), timeout=30)
    return r.status_code


with ThreadPoolExecutor(max_workers=4) as pool:
    codes = list(pool.map(post, range(1, N + 1)))
common.log_kv(log, logging.INFO, "seed_done", total=N, ok=sum(1 for c in codes if c == 200), failed=sum(1 for c in codes if c != 200))
