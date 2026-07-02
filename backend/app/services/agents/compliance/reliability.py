"""Beta-Binomial rule reliability (adaptive rule weights).

Each rule carries pseudo-counts (alpha, beta). Its reliability
θ = alpha / (alpha + beta) is the posterior-mean probability that a finding
fired by this rule is correct, learned from reviewer accept/reject verdicts.
Scoring multiplies the rule's points_deduction by θ.

Safety properties (enforced here, tested in test_reliability.py):
  * NULL counts → θ = 1.0. A rule with no feedback keeps full penalty —
    never silently under-penalize (same principle as H16).
  * RELIABILITY_FLOOR: feedback can discount a rule, never erase it.
  * Damping: the default prior has strength 10, so one verdict moves θ by at
    most 1/(α+β+1) — a single reviewer click cannot whipsaw a rule.
  * Verdict changes revert the prior count first (no double-counting).

The document-level reviewer score is deliberately NOT an input here: it is a
held-out evaluation metric (training on it would be Goodharting the metric).
"""
from typing import Optional, Tuple

#: Prior mean 0.9 with strength 10 — rules are trusted until evidence says
#: otherwise, and early feedback is damped. A corpus-frequency backfill may
#: replace this with stronger per-rule priors.
DEFAULT_PRIOR_ALPHA = 9.0
DEFAULT_PRIOR_BETA = 1.0

#: A rule's penalty can be discounted to 30%, never below: deactivating a
#: rule is a human decision, not an emergent side effect of feedback.
RELIABILITY_FLOOR = 0.3

_VERDICTS = ("accept", "reject")


def theta(alpha: Optional[float], beta: Optional[float]) -> float:
    """Posterior-mean reliability in [RELIABILITY_FLOOR, 1.0].

    Returns 1.0 (full penalty) when counts are missing or corrupt — for a
    compliance tool, fail-safe means never under-penalizing on bad state.
    """
    try:
        a = float(alpha)  # type: ignore[arg-type]
        b = float(beta)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    if a <= 0.0 or b < 0.0 or (a + b) <= 0.0:
        return 1.0
    return max(RELIABILITY_FLOOR, min(1.0, a / (a + b)))


def apply_verdict(
    alpha: Optional[float],
    beta: Optional[float],
    verdict: str,
    previous_verdict: Optional[str] = None,
) -> Tuple[float, float]:
    """Return updated (alpha, beta) after a reviewer verdict on one finding.

    First feedback on a rule initializes the default prior. If the reviewer
    is changing an earlier verdict on the same finding, the earlier count is
    reverted first so the net effect is exactly one verdict.
    """
    if verdict not in _VERDICTS:
        raise ValueError(f"verdict must be one of {_VERDICTS}, got {verdict!r}")
    if previous_verdict is not None and previous_verdict not in _VERDICTS:
        raise ValueError(
            f"previous_verdict must be one of {_VERDICTS}, got {previous_verdict!r}"
        )

    if alpha is None or beta is None:
        a, b = DEFAULT_PRIOR_ALPHA, DEFAULT_PRIOR_BETA
    else:
        a, b = float(alpha), float(beta)

    if previous_verdict == "accept":
        a -= 1.0
    elif previous_verdict == "reject":
        b -= 1.0

    if verdict == "accept":
        a += 1.0
    else:
        b += 1.0

    return max(0.0, a), max(0.0, b)
