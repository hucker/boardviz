"""Streamlit entrypoint. Run with:  uv run streamlit run src/chesstrain/app.py"""

from __future__ import annotations

import streamlit as st

from chesstrain.ui import (
    dashboard,
    import_page,
    inspector_page,
    review_page,
    scout_page,
    trainer_page,
)

st.set_page_config(page_title="chesstrain", page_icon="♟️", layout="wide")

nav = st.navigation([
    st.Page(import_page.render, title="Import", icon="📥",
            url_path="import", default=True),
    st.Page(dashboard.render, title="Dashboard", icon="📊", url_path="dashboard"),
    st.Page(review_page.render, title="Review", icon="🔍", url_path="review"),
    st.Page(trainer_page.render, title="Trainer", icon="🎯", url_path="trainer"),
    st.Page(scout_page.render, title="Scout", icon="🕵️", url_path="scout"),
    st.Page(inspector_page.render, title="Inspector", icon="🔬",
            url_path="inspector"),
])
nav.run()
