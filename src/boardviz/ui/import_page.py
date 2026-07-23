"""Import page: fetch games from chess.com or lichess, then launch engine analysis.

Both steps act on the one **Player** chosen in the sidebar (type a new username
there to fetch someone you haven't imported yet). The two importers share an
``import_user_games(conn, user, n, *, default, tc_class)`` signature, so the page
just picks the module for the chosen source.
"""

from __future__ import annotations

import streamlit as st

from .. import config, db, fetch, lichess
from . import common

_SOURCES = {"chess.com": fetch, "lichess": lichess}


def render() -> None:
    st.header("📥 Import games")
    hosted = config.hosted()  # ENV-HOSTED: page stays visible, actions inert
    if hosted:
        st.warning(
            "**Import isn't available in the hosted demo.** This page shows "
            "what the full app does: pull your games from chess.com or "
            "lichess, then analyze them with Stockfish to build your own "
            "mistake library. Install boardviz locally to use it — "
            "see the [README](https://github.com/hucker/boardviz#readme)."
        )
    conn = common.get_conn()
    profiles = common.list_profiles(conn)

    # --- Fetch. The username is an editable combobox right in the form: pick an
    # imported player, or type a brand-new one to import from the chosen source. ---
    with st.form("fetch_form"):
        c1, c2 = st.columns([2, 1])
        who = c1.selectbox(
            "Username", profiles, index=None, accept_new_options=True,
            placeholder="Pick a player, or type a new username to import",
            help="Type a username to import someone new; pick the site with Source.")
        source = c2.selectbox(
            "Source", list(_SOURCES),
            help="Which site the username is on — game data comes from there.")
        c3, c4 = st.columns(2)
        n = c3.number_input("Games", min_value=1, max_value=2000, value=100, step=10)
        tc = c4.selectbox("Time control", ["(all)"] + common.TC_CLASSES)
        make_default = st.checkbox(
            "Make this my default profile",
            help="The profile the app opens on across pages. The first user you "
                 "import becomes the default automatically.")
        submitted = st.form_submit_button(
            "⬇ Fetch games", type="primary", disabled=hosted)

    if submitted and not who:
        st.warning("Enter a username first — pick one above or type a new one.")
    elif submitted and who:
        tc_class = None if tc == "(all)" else tc
        with st.spinner(f"Fetching last {n} games for {who} from {source}…"):
            try:
                res = _SOURCES[source].import_user_games(
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

    if pending and st.button(f"Analyze {pending} games", type="primary",
                             disabled=hosted):
        common.launch_analyze(who)
        st.session_state["analyzing"] = who
        st.rerun()

    # Live progress (auto-refreshes while running). Reads work off the DB as it
    # fills, so the rest of the app is usable during analysis.
    if st.session_state.get("analyzing") or db.latest_run(conn, who, "analyze"):
        common.analyze_progress(conn, who)
