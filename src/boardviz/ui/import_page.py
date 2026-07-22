"""Import page: fetch games from chess.com, then launch engine analysis.

Both steps act on the one **Player** chosen in the sidebar (type a new username
there to fetch someone you haven't imported yet).
"""

from __future__ import annotations

import streamlit as st

from .. import db, fetch
from . import common


def render() -> None:
    st.header("📥 Import games")
    conn = common.get_conn()
    # The single sidebar Player selector; allow_new lets you type a new username.
    who = common.profile_picker(conn, allow_new=True)

    # --- Fetch ---
    with st.form("fetch_form"):
        c1, c2 = st.columns(2)
        n = c1.number_input("Games", min_value=1, max_value=2000, value=100, step=10)
        tc = c2.selectbox("Time control", ["(all)"] + common.TC_CLASSES)
        make_default = st.checkbox(
            "Make this my default profile",
            help="The profile the app opens on across pages. The first user you "
                 "import becomes the default automatically.")
        label = f"Fetch games for {who}" if who else "Pick or type a player first ↖"
        submitted = st.form_submit_button(label, type="primary", disabled=not who)

    if submitted and who:
        tc_class = None if tc == "(all)" else tc
        with st.spinner(f"Fetching last {n} games for {who}…"):
            try:
                res = fetch.import_user_games(
                    conn, who, int(n), default=make_default, tc_class=tc_class)
                st.success(
                    f"Fetched {res['collected']} games — {res['inserted']} new, "
                    f"{res['collected'] - res['inserted']} already stored.")
                if res["inserted"]:
                    st.info(
                        f"To view just these, open the **Dashboard** and set "
                        f"**Most recent N games = {res['inserted']}** in the sidebar.")
            except Exception as exc:
                st.error(f"Fetch failed: {exc}")

    st.divider()

    # --- Analyze (same player) ---
    if not who or who not in common.list_profiles(conn):
        st.info("Fetch a player above to analyze their games.")
        return

    st.subheader(f"Engine analysis — {who}")
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
