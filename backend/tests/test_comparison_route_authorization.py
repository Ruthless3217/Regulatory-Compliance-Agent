"""Every comparison route, called directly, refuses a stranger.

Not a frontend concern and not tested as one: these call the route functions
themselves with a `user` who does not own the row, which is exactly what an
attacker with a session cookie and a UUID would reach. `comparison:use` is held
by every role, so the permission dependency stops nobody — the ownership check
is the whole defence.

The static guard at the end is the part that keeps this true: a comparison
route added next year without the guard fails this suite instead of shipping.
It is the same device test_visibility_route_coverage.py already uses for
submissions.
"""
import asyncio
import pathlib
import re
import uuid

import pytest
from fastapi import HTTPException

from app.api.routes import comparisons as routes
from app.models.comparison_annotation import ComparisonAnnotation
from app.models.document_comparison import DocumentComparison
from tests.support.fake_session import FakeSession


class _User:
    def __init__(self, role="user"):
        self.id = uuid.uuid4()
        self.role = role


@pytest.fixture
def db():
    return FakeSession()


@pytest.fixture
def owner():
    return _User()


@pytest.fixture
def comparison(db, owner):
    c = DocumentComparison(
        id=uuid.uuid4(),
        title="Q3 brochure vs approved",
        old_content_type="docx",
        new_content_type="docx",
        old_original_content="CONFIDENTIAL: the old wording",
        new_original_content="CONFIDENTIAL: the new wording",
        old_file_path=None,
        new_file_path=None,
        status="completed",
        render_status="completed",
        diff_result={"blocks": []},
        created_by=owner.id,
    )
    db.add(c)
    return c


def _run(coro):
    return asyncio.run(coro)


# Every by-id route, as (name, callable taking (comparison_id, user, db)).
# Kept as data so a new route is one line, and so the stranger tests below
# cannot quietly skip one.
BY_ID_ROUTES = [
    ("get", lambda cid, u, db: routes.get_comparison(comparison_id=cid, user=u, db=db)),
    ("pages", lambda cid, u, db: routes.get_comparison_page(
        comparison_id=cid, side="old", n=1, user=u, db=db)),
    ("search", lambda cid, u, db: routes.search_comparison(
        comparison_id=cid, side="old", q="confidential", user=u, db=db)),
    ("annotation_upsert", lambda cid, u, db: routes.upsert_annotation(
        comparison_id=cid,
        body=routes.AnnotationIn(change_id="c1", note="mine", tags=[]),
        user=u, db=db)),
    ("annotation_delete", lambda cid, u, db: routes.delete_annotation(
        comparison_id=cid, change_id="c1", user=u, db=db)),
    # A REAL export kind: the route validates `kind` before the ownership
    # check, so a bogus one would 404 on the kind and prove nothing about
    # authorization.
    ("export", lambda cid, u, db: routes.export_comparison(
        comparison_id=cid, kind="changes-report.docx", user=u, db=db)),
    ("delete", lambda cid, u, db: routes.delete_comparison(
        comparison_id=cid, user=u, db=db)),
]


# --- B: a stranger is refused everywhere -----------------------------------

@pytest.mark.parametrize("name,call", BY_ID_ROUTES, ids=[n for n, _ in BY_ID_ROUTES])
def test_a_stranger_is_refused_by_every_route(db, comparison, name, call):
    with pytest.raises(HTTPException) as raised:
        _run(call(str(comparison.id), _User(), db))
    assert raised.value.status_code == 404, f"{name} leaked a {raised.value.status_code}"
    assert raised.value.detail == "Comparison not found"


def test_a_stranger_cannot_read_the_document_content(db, comparison):
    # The concrete harm, stated as the test: the row holds both documents.
    with pytest.raises(HTTPException):
        _run(routes.get_comparison(comparison_id=str(comparison.id), user=_User(), db=db))


def test_knowing_the_uuid_buys_nothing(db, comparison):
    # Direct-id access is the attack; the response for a real-but-forbidden id
    # is byte-identical to the one for an id that does not exist.
    stranger = _User()
    with pytest.raises(HTTPException) as real:
        _run(routes.get_comparison(comparison_id=str(comparison.id), user=stranger, db=db))
    with pytest.raises(HTTPException) as fake:
        _run(routes.get_comparison(comparison_id=str(uuid.uuid4()), user=stranger, db=db))
    assert (real.value.status_code, real.value.detail) == (fake.value.status_code, fake.value.detail)


def test_a_refused_delete_does_not_remove_the_row(db, comparison):
    with pytest.raises(HTTPException):
        _run(routes.delete_comparison(comparison_id=str(comparison.id), user=_User(), db=db))
    assert db.rows_for(DocumentComparison) == [comparison]


def test_a_refused_annotation_delete_leaves_the_annotation(db, comparison, owner):
    ann = ComparisonAnnotation(
        id=uuid.uuid4(), comparison_id=comparison.id, change_id="c1",
        note="the owner's note", tags=[], created_by=owner.id,
    )
    db.add(ann)

    with pytest.raises(HTTPException):
        _run(routes.delete_annotation(
            comparison_id=str(comparison.id), change_id="c1", user=_User(), db=db))

    assert db.rows_for(ComparisonAnnotation) == [ann]


def test_a_refused_annotation_upsert_writes_nothing(db, comparison):
    with pytest.raises(HTTPException):
        _run(routes.upsert_annotation(
            comparison_id=str(comparison.id),
            body=routes.AnnotationIn(change_id="c1", note="injected", tags=[]),
            user=_User(), db=db))
    assert db.rows_for(ComparisonAnnotation) == []


# --- A / G / H: the owner and the privileged roles still work --------------

def test_the_owner_can_read_their_own(db, comparison, owner):
    out = _run(routes.get_comparison(comparison_id=str(comparison.id), user=owner, db=db))
    assert out["id"] == str(comparison.id)


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_privileged_roles_still_read_anything(db, comparison, role):
    out = _run(routes.get_comparison(comparison_id=str(comparison.id), user=_User(role), db=db))
    assert out["id"] == str(comparison.id)


def test_the_owner_can_still_annotate(db, comparison, owner):
    out = _run(routes.upsert_annotation(
        comparison_id=str(comparison.id),
        body=routes.AnnotationIn(change_id="c1", note="mine", tags=["wording"]),
        user=owner, db=db))
    assert out["change_id"] == "c1"
    assert len(db.rows_for(ComparisonAnnotation)) == 1


def test_the_owner_can_still_delete_their_own(db, comparison, owner):
    _run(routes.delete_comparison(comparison_id=str(comparison.id), user=owner, db=db))
    assert db.rows_for(DocumentComparison) == []


def test_an_admin_can_still_delete(db, comparison):
    _run(routes.delete_comparison(comparison_id=str(comparison.id), user=_User("admin"), db=db))
    assert db.rows_for(DocumentComparison) == []


# --- the list is scoped, not merely ordered --------------------------------

def test_the_list_shows_a_user_only_their_own(db, owner):
    theirs = DocumentComparison(
        id=uuid.uuid4(), title="mine", old_content_type="text",
        new_content_type="text", created_by=owner.id)
    someone_elses = DocumentComparison(
        id=uuid.uuid4(), title="not mine", old_content_type="text",
        new_content_type="text", created_by=_User().id)
    db.add(theirs)
    db.add(someone_elses)

    out = _run(routes.list_comparisons(skip=0, limit=20, user=owner, db=db))
    assert [c["title"] for c in out["comparisons"]] == ["mine"]
    assert out["total"] == 1


def test_the_list_is_unfiltered_for_an_admin(db, owner):
    for title, uid in (("mine", owner.id), ("theirs", _User().id)):
        db.add(DocumentComparison(
            id=uuid.uuid4(), title=title, old_content_type="text",
            new_content_type="text", created_by=uid))

    out = _run(routes.list_comparisons(skip=0, limit=20, user=_User("admin"), db=db))
    assert out["total"] == 2


# --- the guard that keeps this true ----------------------------------------

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routes"
RAW_LOOKUP = re.compile(
    r"\w*db\.query\(\s*DocumentComparison\s*\)\s*\.\s*filter\(\s*DocumentComparison\.id\s*=="
)
EXEMPT = re.compile(r"#\s*visibility:\s*exempt")


def test_no_comparison_route_resolves_a_row_without_the_guard():
    """Same device as test_visibility_route_coverage.py, for the other resource.

    An exemption is allowed, but only by saying so out loud on the line before:

        # visibility: exempt — <reason>

    which keeps every exception in the diff instead of in an allowlist here.
    """
    src = (ROUTES / "comparisons.py").read_text(encoding="utf-8").splitlines()
    offenders = [
        f"comparisons.py:{i + 1}: {line.strip()}"
        for i, line in enumerate(src)
        if RAW_LOOKUP.search(line) and not (i and EXEMPT.search(src[i - 1]))
    ]
    assert offenders == [], (
        "These resolve a comparison without get_visible_comparison, which is how "
        "any user could read anyone's documents:\n  " + "\n  ".join(offenders)
    )


def test_every_by_id_route_is_covered_by_this_suite():
    """If someone adds a by-id route, BY_ID_ROUTES must grow with it — otherwise
    the stranger tests above silently stop covering the whole surface."""
    src = (ROUTES / "comparisons.py").read_text(encoding="utf-8")
    declared = set(re.findall(r'@router\.\w+\("(/\{comparison_id\}[^"]*)"\)', src))
    assert len(declared) == len(BY_ID_ROUTES), (
        f"{len(declared)} by-id routes exist but {len(BY_ID_ROUTES)} are exercised: {sorted(declared)}"
    )
