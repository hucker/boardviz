"""Smoke test: every page renders in a simulated Streamlit runtime without error.

Exercises the real DB (whatever is in data/), so it covers the with-data render
paths, not just empty state. Uses ``AppTest.from_string`` (not ``from_function``)
so each page runs with its module-level imports intact.
"""

import pytest
from streamlit.testing.v1 import AppTest

PAGES = ["import_page", "dashboard", "review_page", "mate_page", "trainer_page",
         "scout_page"]


class TestPageRendering:
    """Each page renders without raising — a baseline that its surface works."""

    @pytest.mark.spec("IMP-FETCH", "DASH-COUNT", "REV-CLUST", "MATE-GRID",
                      "TRN-DRILL", "SCT-VIEW")
    @pytest.mark.parametrize("module", PAGES)
    def test_page_renders_without_exception(self, module):
        """The page's render() runs to completion with no uncaught exception."""
        # Arrange: a one-line script that imports and renders the page.
        script = f"from chesstrain.ui import {module} as p\np.render()\n"
        # Act.
        app = AppTest.from_string(script).run(timeout=60)
        # Assert.
        assert not app.exception, f"{module} raised: {app.exception}"
