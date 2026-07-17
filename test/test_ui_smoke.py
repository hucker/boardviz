"""Smoke-test every page: render it in a simulated Streamlit runtime and assert
no exception. Exercises the real DB (whatever is in data/), so it also covers
the with-data render paths, not just empty-state.

Uses ``AppTest.from_string`` (not ``from_function``) so each page runs with its
module-level imports intact.
"""

import pytest
from streamlit.testing.v1 import AppTest

PAGES = ["import_page", "dashboard", "review_page", "trainer_page",
         "scout_page", "inspector_page"]


@pytest.mark.parametrize("module", PAGES)
def test_page_renders_without_exception(module):
    script = f"from chesstrain.ui import {module} as p\np.render()\n"
    at = AppTest.from_string(script).run(timeout=60)
    assert not at.exception, f"{module} raised: {at.exception}"
