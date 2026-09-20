# Langfuse tracing

LLM observability for the compliance pipeline: one trace per analysis run or
rewrite, every model call as a `generation` with model / tokens / cost, grouped
into a **session per submission** and attributed to the reviewer. Runs side by
side with the existing LangSmith `@traceable` run tree (both stay on).

Shim: `backend/app/services/observability/tracing.py` — the only module that
imports the `langfuse` SDK. Everything else uses `observe`, `graph_node`,
`trace_root`, `trace_attributes`, `update_span`, `update_generation` from it, so
the pipeline is unaffected (no-ops) when Langfuse is not configured.

## Enable

Set in the root `.env` (container, via compose) **and** `backend/.env` (local
Python) — see `.env.example`:

```
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com   # EU; US = https://us.cloud.langfuse.com
LANGFUSE_ENVIRONMENT=local                      # local | shared | production
LANGFUSE_MASK_PII=true
```

Tracing is on only when both keys are set (and `LANGFUSE_TRACING_ENABLED` is
not `false`). Startup logs `✅ Langfuse tracing enabled` or the reason it is
off. The backend image bakes code and deps: after changing anything here,
`docker compose build backend && docker compose up -d backend`.

## Trace shape

```
analyze-submission                       chain   user=<username> session=<submission_id> tags=[compliance-analysis]
├── preprocess                           chain   input: state summary · output: partial-state summary
├── dispatch                             chain
│   ├── retrieve-rules                   retriever   rule ids per chunk/category
│   ├── retrieve-precedents              retriever   precedents per chunk, degraded chunks → WARNING
│   └── retrieve-product-docs            retriever
├── analysis                             chain
│   └── grade-chunk ×N                   chain   one per chunk (reused chunks say so)
│       ├── llm-structured-response      span    purpose/schema in · parsed JSON out · attempts
│       │   └── precedent_citation       generation  messages, model, usage, cost (langfuse.openai)
│       └── critic-review-violations     evaluator   in/out/dropped/downgraded + per-item verdicts
│           └── llm-structured-response → critic_review generation
├── disclosure                           chain
└── scoring                              chain
    └── calculate-scores                 tool

rewrite-violation                        chain   session=<submission_id> tags=[rewrite]
└── llm-generate-response → rewrite generation

embed-texts                              embedding   counts + dims, never the vectors
```

Generation names are the call's *purpose* (`tool_name` of
`generate_structured_response`, or `purpose=` of `generate_response`); a
schema-retry or key failover shows up as another generation under the same
wrapper span, so retries are visible instead of averaged away. Root output
carries the final status, score, grade and a compact violation list; node
inputs/outputs are counts + ids, never the raw graph state.

## Where to look

- **Traces** — filter by tag `compliance-analysis` / `rewrite`, by user, or by
  `metadata.submission_id` / `run_id` (both match `analysis_runs`).
- **Sessions** — one per submission: every run and rewrite of a document.
- **Dashboards** — cost/latency per model; `LANGFUSE_ENVIRONMENT` keeps local
  runs out of production views.
- A run that was *not* persisted (`needs_review` / `failed`) has a `WARNING`
  root with `status_message = not persisted: <reason>`.

## Privacy

`LANGFUSE_MASK_PII=true` (default) runs `app.services.pii` over every
input/output/metadata value before export: emails, phones, PAN, Aadhaar and
card-like digit runs become `[EMAIL]` etc. Identifier keys (`*_id`, `id`,
`uin`) are exempt so traces stay joinable to the database.

## Gotchas

- **Corporate TLS on the host**: the exporter uses `requests`/certifi; behind
  the Bajaj/Cisco interception run local Python with
  `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` pointing at a certifi bundle with
  `backend/certs/*.pem` appended (the Docker image already does this).
- **v4 project API**: this Langfuse project is on the v4 platform — the legacy
  `/api/public/traces` endpoints return 410. Query with
  `langfuse.api.observations.get_many(trace_id=..., fields="core,basic,io,usage,...")`
  or `npx langfuse-cli`.
- Unit tests never export: `backend/tests/conftest.py` forces
  `LANGFUSE_TRACING_ENABLED=false`; `tests/test_langfuse_tracing.py` asserts
  the trace shape against an in-memory OTel exporter with a mocked provider.
