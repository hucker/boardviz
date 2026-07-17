"""Import page: fetch games from chess.com, then launch engine analysis."""

from __future__ import annotations

import streamlit as st

from .. import db, fetch
from . import common


def render() -> None:
    st.header("📥 Import games")
    conn = common.get_conn()

    with st.form("fetch_form"):
        c1, c2, c3 = st.columns([2, 1, 1])
        username = c1.text_input("chess.com username", value="hucker233")
        n = c2.number_input("Games", min_value=1, max_value=2000, value=100, step=10)
        tc = c3.selectbox("Time control", ["(all)"] + common.TC_CLASSES)
        scout = st.checkbox("Scout mode (store as an opponent, not me)")
        submitted = st.form_submit_button("Fetch games", type="primary")

    if submitted and username:
        tc_class = None if tc == "(all)" else tc
        with st.spinner(f"Fetching last {n} games for {username}…"):
            try:
                res = fetch.import_user_games(
                    conn, username, int(n), is_me=not scout, tc_class=tc_class)
                st.success(
                    f"Fetched {res['collected']} games — "
                    f"{res['inserted']} new, {res['collected'] - res['inserted']} "
                    f"already stored.")
            except Exception as exc:
                st.error(f"Fetch failed: {exc}")

    st.divider()

    # Analysis section: show what's pending and let the user launch the engine.
    profiles = common.list_profiles(conn)
    if not profiles:
        st.info("Fetch some games first.")
        return

    st.subheader("Engine analysis")
    who = st.selectbox("Profile to analyze", profiles)
    pending = len(db.unanalyzed_games(conn, who))
    analyzed = len(db.query_games(conn, username=who, analyzed=1))
    m1, m2 = st.columns(2)
    m1.metric("Unanalyzed games", pending)
    m2.metric("Analyzed games", analyzed)

    if pending and st.button(f"Analyze {pending} games", type="primary"):
        common.launch_analyze(who)
        st.session_state["analyzing"] = who
        st.rerun()

    # Live progress (auto-refreshes while running). Reads work off the DB as it
    # fills, so the rest of the app is usable during analysis.
    if st.session_state.get("analyzing") or db.latest_run(conn, who, "analyze"):
        common.analyze_progress(conn, who)
