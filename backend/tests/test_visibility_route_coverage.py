"""Static guard: every route that resolves a submission must go through
`get_visible_submission`.

This is a source-level assertion rather than 26 request tests, and that is the
point — a NEW route added later without the guard fails this suite instead of
shipping a hole. Request tests would only ever cover the routes someone
remembered to write a test for.

A raw lookup may opt out, but only by saying so out loud on the preceding line:

    # visibility: exempt — <reason>
    submission = db.query(Submission).filter(Submission.id == sid).first()

which keeps every exception visible in review instead of buried in an allowlist
inside this file.
"""
import pathlib
import re

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routes"

# Files carrying at least one submission-scoped route.
# admin_retrieval.py resolves AnalysisRun rather than Submission and is gated on
# rules:write (admin + super_admin, both of whom see everything), so it has no
# submission lookup to guard.
GUARDED_FILES = ("submissions.py", "compliance.py", "similar.py")

# The pattern the guard replaces. `\w*db` also catches `init_db.query(...)`.
RAW_LOOKUP = re.compile(
    r"\w*db\.query\(\s*Submission\s*\)\s*\.\s*filter\(\s*Submission\.id\s*==",
)
EXEMPT = re.compile(r"#\s*visibility:\s*exempt")


def _unguarded(name: str) -> list[str]:
    src = (ROUTES / name).read_text(encoding="utf-8").splitlines()
    offenders = []
    for i, line in enumerate(src):
        if not RAW_LOOKUP.search(line):
            continue
        window = "\n".join(src[max(0, i - 6): i + 1])
        if EXEMPT.search(window):
            continue
        offenders.append(f"{name}:{i + 1}")
    return offenders


def test_no_route_resolves_a_submission_without_the_guard():
    offenders = [o for name in GUARDED_FILES for o in _unguarded(name)]
    assert offenders == [], (
        "Unguarded submission lookups found. Replace each with "
        "`get_visible_submission(db, submission_id, user)`, or mark it "
        "`# visibility: exempt - <reason>`:\n  " + "\n  ".join(offenders)
    )


def test_guarded_files_import_the_guard():
    for name in GUARDED_FILES:
        src = (ROUTES / name).read_text(encoding="utf-8")
        assert "get_visible_submission" in src, f"{name} does not use the visibility guard"


def test_list_submissions_applies_the_set_filter():
    src = (ROUTES / "submissions.py").read_text(encoding="utf-8")
    assert "visible_submission_filter" in src, (
        "list_submissions must scope its query, or the bucket is cosmetic"
    )


def test_exemptions_are_only_the_two_background_helpers():
    """Exemptions are allowed but not meant to accumulate. Both current ones are
    SSE generators that run after the response, with no request context and no
    user to check — the guard belongs on the route that spawns them."""
    src = (ROUTES / "compliance.py").read_text(encoding="utf-8")
    assert len(EXEMPT.findall(src)) == 2
