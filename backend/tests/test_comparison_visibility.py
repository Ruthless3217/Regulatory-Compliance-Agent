"""Who may see a comparison.

A `document_comparisons` row holds BOTH documents in full — `old_original_content`
and `new_original_content` — plus the uploaded files and the rendered page
images. Until this existed, every comparison route resolved a row by id alone
and `comparison:use` is held by every role, so any user who knew (or guessed)
a UUID could read anyone's documents, re-run their comparison over the top of
it, or delete it along with their files.

Ownership is `created_by`, and only that: `document_comparisons` has no
`submission_id`, so there is no assignment to inherit visibility from. Whether
comparisons should join the submission domain is a data-model question and is
deliberately not answered here.

Mirrors test_submission_visibility.py, because the rule is the same rule.
"""
import uuid

import pytest
from fastapi import HTTPException

from app.auth.visibility import (
    get_visible_comparison,
    may_see_comparison,
    visible_comparison_filter,
)
from app.models.document_comparison import DocumentComparison
from tests.support.fake_session import FakeSession


class _User:
    def __init__(self, role="user", uid=None):
        self.id = uid or uuid.uuid4()
        self.role = role


@pytest.fixture
def db():
    return FakeSession()


def _comparison(db, created_by=None):
    c = DocumentComparison(
        id=uuid.uuid4(),
        title="Brochure v1 vs v2",
        old_content_type="docx",
        new_content_type="docx",
        old_original_content="the old wording",
        new_original_content="the new wording",
        created_by=created_by,
    )
    db.add(c)
    return c


# --- the predicate ---------------------------------------------------------

def test_the_creator_sees_their_own(db):
    owner = _User()
    assert may_see_comparison(_comparison(db, owner.id), owner) is True


def test_another_user_does_not(db):
    owner, stranger = _User(), _User()
    assert may_see_comparison(_comparison(db, owner.id), stranger) is False


def test_admin_sees_everything(db):
    assert may_see_comparison(_comparison(db, _User().id), _User("admin")) is True


def test_super_admin_sees_everything(db):
    assert may_see_comparison(_comparison(db, _User().id), _User("super_admin")) is True


def test_an_unowned_row_is_admin_only(db):
    # `created_by` is nullable and rows predating authentication have it NULL.
    # Fail closed: "nobody is recorded as the owner" must not read as
    # "everybody owns it".
    orphan = _comparison(db, created_by=None)
    assert may_see_comparison(orphan, _User()) is False
    assert may_see_comparison(orphan, _User("admin")) is True


def test_a_caller_with_no_id_sees_nothing(db):
    class _Anon:
        id = None
        role = "user"

    assert may_see_comparison(_comparison(db, _User().id), _Anon()) is False


def test_an_unknown_role_sees_nothing(db):
    owner = _User()
    assert may_see_comparison(_comparison(db, owner.id), _User("auditor")) is False


# --- the resolver ----------------------------------------------------------

def test_get_visible_returns_the_row_for_its_owner(db):
    owner = _User()
    c = _comparison(db, owner.id)
    assert get_visible_comparison(db, str(c.id), owner) is c


def test_get_visible_404s_for_a_stranger(db):
    # 404, never 403 — a 403 would confirm the comparison exists, which is
    # itself the disclosure. Same convention as submissions.
    c = _comparison(db, _User().id)
    with pytest.raises(HTTPException) as raised:
        get_visible_comparison(db, str(c.id), _User())
    assert raised.value.status_code == 404
    assert raised.value.detail == "Comparison not found"


def test_a_missing_row_is_indistinguishable_from_a_forbidden_one(db):
    # Knowing a UUID must buy an outsider nothing: both answers are identical.
    c = _comparison(db, _User().id)
    stranger = _User()

    with pytest.raises(HTTPException) as forbidden:
        get_visible_comparison(db, str(c.id), stranger)
    with pytest.raises(HTTPException) as missing:
        get_visible_comparison(db, str(uuid.uuid4()), stranger)

    assert forbidden.value.status_code == missing.value.status_code == 404
    assert forbidden.value.detail == missing.value.detail


def test_get_visible_lets_an_admin_through(db):
    c = _comparison(db, _User().id)
    assert get_visible_comparison(db, str(c.id), _User("admin")) is c


# --- the list filter -------------------------------------------------------

def test_the_filter_is_none_for_unrestricted_roles():
    assert visible_comparison_filter(_User("admin")) is None
    assert visible_comparison_filter(_User("super_admin")) is None


def test_the_filter_matches_nothing_for_a_caller_with_no_id():
    # `created_by == None` would render as IS NULL and list exactly the unowned
    # rows may_see_comparison refuses. The two helpers must not disagree.
    class _Anon:
        id = None
        role = "user"

    clause = visible_comparison_filter(_Anon())
    assert clause is not None
    assert "false" in str(clause).lower()


def test_the_filter_scopes_an_ordinary_caller_to_their_own():
    user = _User()
    clause = visible_comparison_filter(user)
    assert clause is not None
    assert clause.left.key == "created_by"
    assert clause.right.value == user.id
