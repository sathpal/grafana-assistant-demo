# grafana

- `push_grafana.py` — dashboards-as-code: builds and pushes the **Publishing tier** dashboard (uid `cortex-publishing-tier`)
  and five alert rules (group `cortex-publishing-tier`, folder `CORTEX-AKS`) using `GRAFANA_URL` and `GRAFANA_SA_TOKEN`
  from `.env`. Idempotent. `make grafana`.
- `dashboards/cortex-publishing-tier.json` — the generated dashboard, kept for review and for importing by hand.
- `runbook.md` — what each alert means, what to check first, and which chaos scenario produces it.

The assistant's own writes in Part 5 (an `rca` annotation, the `PublishedContentGap` rule, the "Consumed vs published"
panel) are made through the MCP server, not this script; `demos/p3_writes.sh` reproduces them.
