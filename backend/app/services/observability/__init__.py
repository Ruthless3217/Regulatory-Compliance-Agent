"""Observability: token/cost metering + append-only audit trail.

Phase 4 modules (see ``audit-trail/02-token-cost-monitoring.md`` and
``audit-trail/05-backend-implementation.md``). All DB writes here are
*best-effort*: losing a metering or audit row must never break a grade.
"""
