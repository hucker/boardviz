"""Streamlit entrypoint. Run with:  uv run streamlit run src/boardviz/app.py"""

from __future__ import annotations

# Import the mounted source, not a stale installed copy. Streamlit runs this
# file by path, so the *package* dir lands on sys.path but its parent (the src
# root) does not — leaving `import boardviz` to resolve to whatever is in
# site-packages. On Streamlit Community Cloud that installed build is only
# rebuilt when uv.lock/version changes, so source-only pushes would import
# stale modules (an older `config` missing new functions -> AttributeError).
# Prepending the src root makes a fresh push always win. Harmless locally.
import sys
from pathlib import Path

_SRC_ROOT = str(Path(__file__).resolve().parent.parent)
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

import streamlit as st  # noqa: E402

from boardviz import bootstrap, config  # noqa: E402
from boardviz.ui import common  # noqa: E402

st.set_page_config(page_title="boardviz", page_icon="♟️", layout="wide")

# `streamlit run … -- --hosted` forces demo mode without env vars (ENV-HOSTED).
config.promote_cli_flags()

# Sample-DB bootstrap (ENV-DEMO): with no data yet, fetch the sample database
# so a bare clone boots with games to explore. A failed download must never
# block startup — the app just starts empty.
try:
    if bootstrap.ensure_db():
        st.toast("Sample database downloaded — exploring demo games.")
except Exception as exc:  # noqa: BLE001 — best-effort by spec (ENV-DEMO)
    st.warning(f"Sample database unavailable ({exc}); starting empty.")

# Trim Streamlit's generous top padding to reclaim the header gap — noticeable
# wasted space on a small screen / phone. (A deliberate CSS escape hatch: there
# is no native/theme option for the block-container padding.)
st.markdown(
    "<style>.block-container,[data-testid='stMainBlockContainer']"
    "{padding-top:1.8rem !important;}</style>",
    unsafe_allow_html=True,
)

# Hosted demo (ENV-HOSTED): no Import page, Dashboard is the landing page.
nav = st.navigation(common.nav_pages(config.hosted()))
nav.run()
