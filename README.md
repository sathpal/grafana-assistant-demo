# grafana-assistant-demo

An **open-source AI assistant for Grafana Cloud**, and a polyglot pipeline for it to debug.

The assistant is two Apache-2.0 projects wired together: [goose](https://github.com/block/goose) (Block's agent) talking to
Grafana Cloud through [mcp-grafana](https://github.com/grafana/mcp-grafana) (Grafana Labs' MCP server). The pipeline is a
content-publishing tier in four languages (Python, Java, Go, Python batch) with Kafka, a Redis cache tier and Postgres,
instrumented with OpenTelemetry and shipped to Grafana Cloud by Alloy. Chaos toggles break it in three realistic ways;
scripted demos show the assistant finding each root cause and, in one case, writing the fix back into Grafana.

The six-part article series that goes with this repo lives in
[sathpal/grafana-articles](https://github.com/sathpal/grafana-articles).

```text
  you ──prompt──▶ goose ──MCP tools──▶ mcp-grafana ──Grafana API──▶ Grafana Cloud (Mimir · Loki · Tempo · Alerting)
                                                                            ▲
  approvals-api (Python) ─▶ Kafka ─▶ publisher-service (Java) ─▶ media-service (Go) ─▶ cache-redis        │ OTLP + scrapes + logs
                    content-batch (Python) ─▶ Kafka           ─▶ Postgres        site-reader (Python) ─▶ Alloy ─┘
```

## Quick start

```bash
git clone https://github.com/sathpal/grafana-assistant-demo && cd grafana-assistant-demo
cp .env.example .env            # Grafana Cloud OTLP credentials + GRAFANA_URL + a service-account token
make check
make up && make seed            # ~5 min the first time (Maven + Go + pip builds), then 300 articles published
make grafana                    # dashboard "Publishing tier" + 5 alert rules in folder CORTEX-AKS

go install github.com/grafana/mcp-grafana/cmd/mcp-grafana@latest
make mcp &                      # the Grafana MCP server on localhost:8300 (8000 belongs to the approvals API)

make demo P=1                   # the pieces and one approval end to end (see demos/README.md for all six)
```

Without a model key the demos still show every piece of evidence the assistant would gather (through the same MCP calls);
with `goose` and a provider key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or Ollama) they run the recipes too.

## Sub-projects

| folder | what | language |
|---|---|---|
| [`services/approvals-api`](services/approvals-api) | the human gate: approve an article, produce to Kafka with trace context in the headers | Python, FastAPI, aiokafka |
| [`services/publisher-service`](services/publisher-service) | consumes both content topics, renders, calls media-service, writes Postgres, caches in Redis, serves `GET /published/{id}`; chaos: `slow-render`, `db-write-fail`, `heap-pressure`, `consumer-pause` | Java 21, Spring Boot 3.4, OTel Java agent |
| [`services/media-service`](services/media-service) | renders OG images, caches 20 KiB blobs per channel variant in `cache-redis`, announces on Kafka; chaos: `media-slow-render`, `cache-stampede`, `no-ttl`, `goroutine-leak` | Go 1.23, OTel Go SDK |
| [`services/content-batch`](services/content-batch) | the reindex batch (`batch.py`), synthetic readers (`reader.py`), the seeder (`seed.py`); chaos: `batch-flood`, `batch-flood-media` | Python |
| [`assistant/`](assistant) | goose recipes with the house rules, `mcp_call.py` (call any MCP tool), `evidence.py` (the queries an on-call assistant runs, printed) | Python, YAML |
| [`demos/`](demos) | one narrated, scripted demo per article part (`make demo P=1..6`) | bash |
| [`grafana/`](grafana) | dashboards-as-code (`push_grafana.py`), the alert rules, the runbook | Python, JSON |
| [`infra/alloy`](infra/alloy) | the Alloy pipeline: OTLP in, exporter scrapes, container logs, Grafana Cloud out | Alloy |
| [`scripts/chaos.sh`](scripts/chaos.sh) | toggles a scenario and writes a `cortex` + `change` annotation to Grafana | bash |

## The three incidents

| `make chaos S=` | what breaks | article |
|---|---|---|
| `batch-flood` | 3,000 reindex messages every 5 min; approved articles queue behind them (peak lag ~5,000, ~66 s per approval) | Part 3 |
| `no-ttl`, then `batch-flood-media` | images cached without expiry, then 6,000 forced renders fill the 48 MiB cache tier: evictions, 0 % hit ratio, lag | Part 4 |
| `db-write-fail` | Postgres rejects every write but the consumer commits its offset: lag 0, green dashboards, 404s | Part 5 |

`make chaos S=reset` clears everything. Every toggle and every batch run writes a Grafana annotation, which is what the
assistant reads first.

## Ports and names

Containers are named `cortex-*` so the `service_name` labels match the dashboards, the alert rules and the articles.
Host ports: approvals-api 8000, publisher 8085, media 8086, batch 8087, Alloy UI 12345. Docker Desktop needs about 2 GiB
for the tier. The MCP server runs on 8300.

## Guard rails (Part 6, in one paragraph)

Run `mcp-grafana --disable-write` with a Viewer token by default; hand out an Editor token only for a session that is
meant to write; trim the tool list with `--disable-*`; run one server per team behind `--server-auth-token`; keep the
model pinned and the recipes in git; cap runs with `--max-tool-repetitions`. The server log is the audit trail.
