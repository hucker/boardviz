"""Import page: fetch games from chess.com, then launch engine analysis."""

from __future__ import annotations

import streamlit as st

from .. import db, fetch
from . import common


def render() -> None:
    st.header("📥 Import games")
    conn = common.get_conn()
    profiles = common.list_profiles(conn)

    with st.form("fetch_form"):
        c1, c2, c3 = st.columns([2, 1, 1])
        username = c1.selectbox(
            "chess.com username", profiles, accept_new_options=True,
            index=common.profile_index(conn, profiles) if profiles else None,
            placeholder="Pick a profile or type a new username",
            help="Existing profiles are listed; type any chess.com username and "
                 "press Enter to add a new one.")
        n = c2.number_input("Games", min_value=1, max_value=2000, value=100, step=10)
        tc = c3.selectbox("Time control", ["(all)"] + common.TC_CLASSES)
        make_default = st.checkbox(
            "Make this my default profile",
            help="The profile the app opens on across pages. The first user you "
                 "import becomes the default automatically.")
        submitted = st.form_submit_button("Fetch games", type="primary")

    if submitted and username:
        tc_class = None if tc == "(all)" else tc
        with st.spinner(f"Fetching last {n} games for {username}…"):
            try:
                res = fetch.import_user_games(
                    conn, username, int(n), default=make_default, tc_class=tc_class)
                st.success(
                    f"Fetched {res['collected']} games — "
                    f"{res['inserted']} new, {res['collected'] - res['inserted']} "
                    f"already stored.")
                if res["inserted"]:
                    st.info(
                        f"To view just these, open the **Dashboard** and set "
                        f"**Most recent N games = {res['inserted']}** in the "
                        f"sidebar.")
            except Exception as exc:
                st.error(f"Fetch failed: {exc}")

    st.divider()

    # Analysis section: show what's pending and let the user launch the engine.
    profiles = common.list_profiles(conn)
    if not profiles:
        st.info("Fetch some games first.")
        return

    st.subheader("Engine analysis")
    who = st.selectbox("Profile to analyze", profiles,
                       index=common.profile_index(conn, profiles))
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
