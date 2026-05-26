import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routes import knowledge_base


def test_router_prefix_and_routes():
    paths = {r.path for r in knowledge_base.router.routes}
    assert "/knowledge-base/ingest" in paths
    assert "/knowledge-base/stats" in paths


def test_registered_in_app():
    from app.main import app
    paths = {r.path for r in app.routes}
    assert "/knowledge-base/stats" in paths
