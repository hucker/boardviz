"""Streamlit entrypoint. Run with:  uv run streamlit run src/boardviz/app.py"""

from __future__ import annotations

import streamlit as st

from boardviz import bootstrap, config
from boardviz.ui import common

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
