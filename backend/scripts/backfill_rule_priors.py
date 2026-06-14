"""Backfill corpus-frequency priors for adaptive rule weights.

Sets each rule's initial Beta pseudo-counts (reliability_alpha/beta) from how
often the rule has historically fired (violation count). Frequency buys prior
STRENGTH (resistance to early reviewer whipsaw), never a different prior MEAN
— a frequently-fired rule isn't assumed more correct, just harder to move
without evidence.

Safety:
  * Never overwrites a rule whose counts are already set (feedback has begun
    or a prior backfill ran) — idempotent by construction.
  * Auto-generated rules (the known-noisy KB segment) get the MINIMUM prior
    strength regardless of frequency, so reviewer feedback corrects them fast.
  * --dry-run (default) prints the plan; --apply writes it.

Usage:
    python -m scripts.backfill_rule_priors            # dry run
    python -m scripts.backfill_rule_priors --apply    # persist
"""
import argparse
import logging
import sys
from typing import Tuple

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

#: Prior mean — matches DEFAULT_PRIOR (9, 1) at zero history.
PRIOR_MEAN = 0.9
#: Strength at n=0; equals DEFAULT_PRIOR_ALPHA + DEFAULT_PRIOR_BETA.
MIN_PRIOR_STRENGTH = 10.0
#: Cap so no rule becomes effectively immune to feedback.
MAX_PRIOR_STRENGTH = 100.0
#: Pseudo-counts gained per historical firing.
STRENGTH_PER_FIRING = 0.5


def prior_counts(n_firings: int) -> Tuple[float, float]:
    """(alpha0, beta0) for a rule that has fired n times historically.

    Strength S = clamp(MIN + λ·n, MIN, MAX); alpha0 = mean·S, beta0 = (1−mean)·S.
    """
    if n_firings < 0:
        raise ValueError(f"n_firings must be >= 0, got {n_firings}")
    strength = min(MAX_PRIOR_STRENGTH, MIN_PRIOR_STRENGTH + STRENGTH_PER_FIRING * n_firings)
    return PRIOR_MEAN * strength, (1.0 - PRIOR_MEAN) * strength


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist (default: dry run)")
    args = parser.parse_args()

    from sqlalchemy import func

    from app.database import SessionLocal
    from app.models.rule import Rule
    from app.models.violation import Violation

    db = SessionLocal()
    try:
        firing_counts = dict(
            db.query(Violation.rule_id, func.count(Violation.id))
            .filter(Violation.rule_id.isnot(None))
            .group_by(Violation.rule_id)
            .all()
        )

        rules = (
            db.query(Rule)
            .filter(Rule.is_active == True)  # noqa: E712
            .filter(Rule.reliability_alpha.is_(None))
            .all()
        )

        planned = 0
        for rule in rules:
            n = int(firing_counts.get(rule.id, 0))
            if rule.is_auto_generated:
                # Known-noisy segment (KB audit 2026-06-02): weakest prior so
                # reviewer feedback corrects these rules quickly.
                alpha, beta = prior_counts(0)
                tag = "auto-gen→min-prior"
            else:
                alpha, beta = prior_counts(n)
                tag = f"n={n}"
            logger.info(
                f"{'WOULD SET' if not args.apply else 'SET'} rule {rule.id} "
                f"[{rule.severity}] ({tag}): α0={alpha:.1f} β0={beta:.1f} "
                f"(strength {alpha + beta:.0f})"
            )
            if args.apply:
                rule.reliability_alpha = alpha
                rule.reliability_beta = beta
            planned += 1

        if args.apply:
            db.commit()
            logger.info(f"Applied priors to {planned} rules.")
        else:
            logger.info(f"Dry run: {planned} rules would be updated. Re-run with --apply.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
