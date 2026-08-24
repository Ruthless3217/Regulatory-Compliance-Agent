"""The Alembic chain is a single unbroken line.

The backend's container CMD is `alembic upgrade head && uvicorn ...`, so a
broken chain is not a migration problem — it is a crash-loop. This repo has hit
it twice: once at 0018 (two heads after a branch merge) and once when 0041 was
committed while the 0038-0040 it depends on were still untracked.

Both failures are structural and cheap to catch here, so this asserts the shape
of the whole `versions/` directory rather than pinning individual revisions.
"""
import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

_REV = re.compile(r'^revision: str = "([^"]+)"', re.M)
_DOWN = re.compile(r'^down_revision: Union\[str, None\] = (?:"([^"]+)"|None)', re.M)


def _chain():
    """revision -> down_revision, for every migration file on disk."""
    out = {}
    for path in sorted(VERSIONS.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        rev, down = _REV.search(src), _DOWN.search(src)
        if not rev:
            continue
        out[rev.group(1)] = (down.group(1) if down else None, path.name)
    return out


@pytest.fixture(scope="module")
def chain():
    c = _chain()
    assert c, "no migrations found — has the versions directory moved?"
    return c


def test_every_down_revision_exists(chain):
    """The 0041-without-0040 failure: `Can't locate revision '0040'`."""
    missing = {
        f"{name} -> {down}"
        for down, name in chain.values()
        if down is not None and down not in chain
    }
    assert not missing, f"migrations pointing at revisions that do not exist: {sorted(missing)}"


def test_there_is_exactly_one_head(chain):
    """The 0018 failure: two migrations claiming the same parent leaves Alembic
    with two heads and no way to pick one."""
    claimed = [down for down, _name in chain.values() if down is not None]
    heads = sorted(set(chain) - set(claimed))
    assert len(heads) == 1, f"expected a single head, found {heads}"


def test_no_two_migrations_share_a_parent(chain):
    """The same fault as above, reported where it is actionable — naming the
    pair that collided rather than the heads they produced."""
    seen: dict = {}
    collisions = []
    for rev, (down, name) in sorted(chain.items()):
        if down is None:
            continue
        if down in seen:
            collisions.append(f"{seen[down]} and {name} both follow {down}")
        seen[down] = name
    assert not collisions, "; ".join(collisions)


def test_exactly_one_base(chain):
    bases = sorted(r for r, (down, _n) in chain.items() if down is None)
    assert len(bases) == 1, f"expected a single base revision, found {bases}"


def test_the_chain_reaches_every_migration(chain):
    """Walking back from the head must visit everything — anything it misses is
    stranded on a fork that `upgrade head` would silently never apply."""
    claimed = {down for down, _n in chain.values() if down is not None}
    head = (set(chain) - claimed).pop()

    walked, cursor = set(), head
    while cursor is not None:
        assert cursor not in walked, f"cycle in the migration chain at {cursor}"
        walked.add(cursor)
        cursor = chain[cursor][0]

    stranded = sorted(set(chain) - walked)
    assert not stranded, f"migrations not reachable from head {head}: {stranded}"


def test_every_migration_is_tracked_by_git(chain):
    """The chain above is read from disk, and disk is not what a clone gets.

    An untracked migration is present for the developer who wrote it and absent
    for everyone else, so every other assertion in this file passes in the one
    working tree where the fault cannot be observed. That is precisely how
    `0041` came to be committed on top of an untracked `0040`: on disk the chain
    was whole. Only a fresh checkout raised `Can't locate revision '0040'`.

    The index is the right thing to compare against rather than HEAD: a file
    staged now is in the commit about to be made, which is the question being
    asked.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "ls-files", "--", str(VERSIONS)],
            capture_output=True, text=True, timeout=30, cwd=str(VERSIONS),
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"git unavailable: {exc}")
    if out.returncode != 0:  # pragma: no cover - not a checkout (sdist, docker COPY)
        pytest.skip("not a git working tree")

    tracked = {line.rsplit("/", 1)[-1] for line in out.stdout.split()}
    if not tracked:  # pragma: no cover
        pytest.skip("no tracked migrations — not a git working tree")

    untracked = sorted(name for _down, name in chain.values() if name not in tracked)
    assert not untracked, (
        "migrations on disk but not in git — a clone would not get these, and "
        f"`alembic upgrade head` there fails on the first one: {untracked}"
    )
