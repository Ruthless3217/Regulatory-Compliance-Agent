"""Output validation for the precedent analysis path.

validate_agent_output checks a single violation-like dict against the new
precedent vocabulary. The analysis node uses it to retry once and then
log+continue — it never raises into the graph.
"""
from __future__ import annotations

from typing import Any, List, Tuple

ALLOWED_SEVERITIES = {"critical", "moderate", "informational"}


def validate_agent_output(output: Any) -> Tuple[bool, List[str]]:
 errors: List[str] = []
 if not isinstance(output, dict):
 return False, ["output is not a dict"]

 vf = output.get("violation_found")
 if vf is not None and not isinstance(vf, bool):
 errors.append("violation_found must be a boolean if present")

 category = output.get("category")
 if not isinstance(category, str) or not category.strip():
 errors.append("category must be a non-empty string")

 severity = output.get("severity")
 if severity not in ALLOWED_SEVERITIES:
 errors.append(f"severity must be one of {sorted(ALLOWED_SEVERITIES)}")

 description = output.get("description")
 if not isinstance(description, str) or len(description.strip()) < 10:
 errors.append("description must be a string of at least 10 characters")

 for key in ("score_impact", "confidence"):
 if key in output and output[key] is not None:
 val = output[key]
 if isinstance(val, bool) or not isinstance(val, (int, float)) or not (0.0 <= float(val) <= 1.0):
 errors.append(f"{key} must be a number in [0, 1]")

 return (len(errors) == 0), errors
