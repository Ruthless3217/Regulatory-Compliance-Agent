import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routes import comparisons


def test_router_prefix_and_routes():
    paths = {r.path for r in comparisons.router.routes}
    assert "/comparisons" in paths
    assert "/comparisons/{comparison_id}" in paths


def test_registered_in_app():
    from app.main import app
    paths = {r.path for r in app.routes}
    assert "/comparisons" in paths
