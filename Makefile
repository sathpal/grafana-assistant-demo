SHELL := /bin/bash
COMPOSE := docker compose --env-file .env -f docker-compose.yml -p cortex
SVCS    := postgres alloy kafka kafka-exporter cache-redis redis-exporter approvals-api media-service publisher-service content-batch site-reader
.DEFAULT_GOAL := help
.PHONY: help up down purge seed batch chaos status grafana logs demo mcp check oss-up oss-down mcp-oss grafana-oss mcp-assistant pub-up pub-down pub-seed pub-batch pub-chaos pub-status pub-grafana pub-logs

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check: ## Verify .env, docker, and the Grafana Cloud credentials
	@test -f .env || { echo "✗ .env missing — cp .env.example .env and fill it in"; exit 1; }
	@docker info >/dev/null 2>&1 && echo "✓ docker" || { echo "✗ docker not running"; exit 1; }
	@grep -qE '^GRAFANA_CLOUD_API_TOKEN=.+' .env && echo "✓ GRAFANA_CLOUD_API_TOKEN set" || echo "✗ GRAFANA_CLOUD_API_TOKEN empty (OTLP ingest)"
	@grep -qE '^GRAFANA_SA_TOKEN=.+' .env && echo "✓ GRAFANA_SA_TOKEN set" || echo "✗ GRAFANA_SA_TOKEN empty (dashboards, alerts, annotations, MCP)"

up: ## Build + start the whole tier, shipping telemetry to Grafana Cloud
	$(COMPOSE) up -d --build $(SVCS)
	@echo "✓ up · approvals :8000 · publisher :8085 · media :8086 · batch :8087 · alloy UI :12345"
down: ## Stop everything, keep volumes
	$(COMPOSE) down
purge: ## Stop everything and delete volumes
	$(COMPOSE) down -v
seed: ## Publish 300 articles through the Java publisher
	$(COMPOSE) exec content-batch python seed.py
batch: ## Run one reindex batch now
	@curl -s -X POST localhost:8087/run; echo
chaos: ## Toggle a scenario: make chaos S=batch-flood [ON=off]   (S=reset clears all)
	@bash scripts/chaos.sh $(S) $(or $(ON),on)
status: ## Chaos + batch state
	@echo "publisher:"; curl -s localhost:8085/chaos; echo; echo "media:"; curl -s localhost:8086/chaos; echo; echo "batch:"; curl -s localhost:8087/config; echo
grafana: ## Push the dashboard + 5 alert rules to Grafana Cloud (dashboards-as-code)
	python3 grafana/push_grafana.py
logs: ## Tail the app services
	$(COMPOSE) logs -f --tail=100 approvals-api publisher-service media-service content-batch site-reader
mcp: ## Start the Grafana MCP server on :8300 with the service-account token from .env
	@GRAFANA_URL=$$(grep -E '^GRAFANA_URL=' .env | cut -d= -f2-) GRAFANA_SERVICE_ACCOUNT_TOKEN=$$(grep -E '^GRAFANA_SA_TOKEN=' .env | cut -d= -f2-) \
	  $${MCP_GRAFANA_BIN:-$$(command -v mcp-grafana || echo $$HOME/go/bin/mcp-grafana)} -t streamable-http -address localhost:8300
demo: ## Run one part of the series demo: make demo P=3   (DEMO_AUTO=1 skips pauses)
	@bash demos/part$(or $(P),1).sh

# aliases used by the articles and demo scripts
pub-up: up
pub-down: down
pub-seed: seed
pub-batch: batch
pub-chaos: chaos
pub-status: status
pub-grafana: grafana
pub-logs: logs

# ---- self-hosted Grafana OSS target (the same assistant, no cloud) -----------------------------
oss-up: ## Start Grafana OSS + Prometheus + Loki + Tempo locally (:3001) and make Alloy ship to both destinations
	$(COMPOSE) --profile oss up -d lgtm
	ALLOY_CONFIG=config.dual.alloy $(COMPOSE) up -d --force-recreate alloy
	@echo "✓ Grafana OSS http://localhost:3001 (admin/admin) · Alloy now ships to Grafana Cloud AND local LGTM"
oss-down: ## Stop the local Grafana OSS stack and put Alloy back to Cloud only
	$(COMPOSE) --profile oss rm -sf lgtm
	$(COMPOSE) up -d --force-recreate alloy
mcp-oss: ## Start a second Grafana MCP server on :8310, pointed at the self-hosted Grafana OSS
	@GRAFANA_URL=http://localhost:3001 GRAFANA_USERNAME=$${GF_OSS_USER:-admin} GRAFANA_PASSWORD=$${GF_OSS_PASSWORD:-admin} \
	  $${MCP_GRAFANA_BIN:-$$(command -v mcp-grafana || echo $$HOME/go/bin/mcp-grafana)} -t streamable-http -address localhost:8310
grafana-oss: ## Push the dashboard + alert rules to the self-hosted Grafana OSS
	GRAFANA_TARGET=oss python3 grafana/push_grafana.py

# ---- bridge to the managed Grafana Assistant (Cloud stacks with the Assistant plugin) --------------
# mcp-grafana's "assistant" category is OPT-IN: it is not in the default --enabled-tools list, and it
# registers ask_assistant only when write tools are enabled (the managed assistant can write).
MCP_DEFAULT_TOOLS := search,datasource,incident,prometheus,loki,alerting,dashboard,folder,oncall,asserts,sift,pyroscope,navigation,tempo,annotations,rendering,snapshot,plugin,api,config,provisioning,docs,user
mcp-assistant: ## Start mcp-grafana on :8320 with ask_assistant enabled (bridges to the Cloud Grafana Assistant plugin)
	@GRAFANA_URL=$$(grep -E '^GRAFANA_URL=' .env | cut -d= -f2-) GRAFANA_SERVICE_ACCOUNT_TOKEN=$$(grep -E '^GRAFANA_SA_TOKEN=' .env | cut -d= -f2-) \
	  $${MCP_GRAFANA_BIN:-$$(command -v mcp-grafana || echo $$HOME/go/bin/mcp-grafana)} -t streamable-http -address localhost:8320 --enabled-tools=$(MCP_DEFAULT_TOOLS),assistant
