---
type: Change Log
title: Bundle Change Log
description: Chronological history of changes to this OKF knowledge bundle.
resource: ./log.md
tags: [okf, log, history]
timestamp: 2026-07-03T12:00:00Z
---

# Change Log

## 2026-07-12 — Compare viewer clone

- Full-screen Compare viewer (Draftable-style) in a new `(viewer)` route group; `/compare/[id]` opens in a new tab.
  New UI module `frontend/components/compare-viewer/*`.
- Pixel "Document view" backend now wired (**PDF-only**): `render_orchestrator.run_render` (BackgroundTask),
  `GET /comparisons/{id}/pages/{side}/{n}`, `_serialize` returns render fields. `pdf_render_service.to_pdf` is
  PDF-passthrough; `gotenberg_client.py` kept as the DOCX re-enable seam.
- Move detection (`moved` blocks + changes), notes & tags (`comparison_annotations`, migration `0020`), positioned
  search (`…/search`), export pack (`…/export/{kind}`), and Adjust-Comparison re-run (`…/rerun`).
- Updated pages: services (index, comparison-service), data-model (document-comparisons), frontend (routing), api (index).

## 2026-07-03 — v0.1 initial bundle

- Created the OKF bundle for the Regulatory Compliance Agent.
- Captured concepts across seven sections: architecture, services, rag, data-model, api, frontend, config.
- Sourced from the repository code and `docs/ARCHITECTURE.md` as of commit `9145ce8` (branch `main`).
- Notes recorded where the `README.md` is stale relative to the deployed system (6-node pipeline on Azure gpt-5.4, not the
  5-node/Gemini description in the README).

> Maintenance convention: when a concept's underlying code changes, update the concept's `timestamp` and add a dated line here.
