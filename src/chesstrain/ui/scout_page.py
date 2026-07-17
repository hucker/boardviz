"""Scout page: import and analyze any opponent, then study their mistakes."""

from __future__ import annotations

import streamlit as st

from .. import db, fetch
from . import common
from . import review_page


def render() -> None:
    st.header("🕵️ Scout an opponent")
    conn = common.get_conn()

    with st.form("scout_fetch"):
        c1, c2, c3 = st.columns([2, 1, 1])
        opp = c1.text_input("Opponent username")
        n = c2.number_input("Games", 1, 1000, 50, 10)
        tc = c3.selectbox("Time control", ["(all)"] + common.TC_CLASSES)
        go = st.form_submit_button("Fetch opponent", type="primary")
    if go and opp:
        with st.spinner(f"Fetching {opp}…"):
            res = fetch.import_user_games(
                conn, opp, int(n), is_me=False,
                tc_class=None if tc == "(all)" else tc)
        st.success(f"{res['inserted']} new / {res['collected']} fetched.")

    opponents = common.list_profiles(conn, is_me=0)
    if not opponents:
        st.info("Fetch an opponent above to begin.")
        return

    who = st.selectbox("Scouted opponent", opponents)
    pending = len(db.unanalyzed_games(conn, who))
    if pending and st.button(f"Analyze {pending} games", type="primary"):
        common.launch_analyze(who)
        st.rerun()
    common.analyze_progress(conn, who)

    st.divider()
    # Their recurring mistakes = moves by the tracked (scouted) player: is_me=1.
    review_page._review_body(conn, {"username": who}, is_me=1, who=who)
