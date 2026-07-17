"""Shared Streamlit helpers: cached DB connection, profile/filter widgets, and
launching the analysis subprocess.
"""

from __future__ import annotations

import subprocess
import sys

import streamlit as st

from .. import db

TC_CLASSES = ["bullet", "blitz", "rapid", "daily"]


@st.cache_resource
def get_conn():
    """One process-wide connection (WAL; reads see the subprocess's commits)."""
    conn = db.connect()
    db.init_db(conn)
    return conn


def list_profiles(conn, is_me: int | None = None) -> list[str]:
    sql = "SELECT username FROM players"
    params: list = []
    if is_me is not None:
        sql += " WHERE is_me=?"
        params.append(is_me)
    sql += " ORDER BY is_me DESC, username"
    return [r["username"] for r in conn.execute(sql, params).fetchall()]


def launch_analyze(username: str) -> subprocess.Popen:
    """Start `chesstrain analyze` as a detached subprocess (owns its engine)."""
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW  # no console popup
    return subprocess.Popen(
        [sys.executable, "-m", "chesstrain.cli", "analyze", "--user", username],
        creationflags=creationflags,
    )


def game_filter_sidebar(conn, key: str) -> dict:
    """Render sidebar game filters and return a filter dict for patterns/db."""
    profiles = list_profiles(conn)
    with st.sidebar:
        st.subheader("Filters")
        username = st.selectbox("Profile", profiles or ["(none)"], key=f"{key}_user")
        tc = st.selectbox("Time control", ["(all)"] + TC_CLASSES, key=f"{key}_tc")
        color = st.selectbox("Color", ["(all)", "white", "black"], key=f"{key}_color")
        outcome = st.selectbox("Result", ["(all)", "win", "loss", "draw"],
                               key=f"{key}_out")
        opening = st.text_input("Opening contains", key=f"{key}_opening",
                                placeholder="e.g. French")
        flagged = st.selectbox(
            "Flagged", ["(all)", "Flag losses only", "Exclude flag losses"],
            key=f"{key}_flag")
        analysis = st.selectbox(
            "Analysis", ["(all)", "Analyzed", "Not analyzed"],
            key=f"{key}_analyzed")
    gf: dict = {}
    if profiles:
        gf["username"] = username
    if tc != "(all)":
        gf["tc_class"] = tc
    if color != "(all)":
        gf["my_color"] = color
    if outcome != "(all)":
        gf["outcome"] = outcome
    if opening.strip():
        gf["opening"] = opening.strip()
    if flagged != "(all)":
        gf["flagged"] = 1 if flagged == "Flag losses only" else 0
    if analysis != "(all)":
        gf["analyzed"] = 1 if analysis == "Analyzed" else 0
    return gf


def analyze_progress(conn, username: str) -> None:
    """Show a progress bar for a running analyze job; auto-refresh until done."""
    from streamlit_autorefresh import st_autorefresh

    run = db.latest_run(conn, username, "analyze")
    if not run:
        return
    if run["status"] == "running":
        total = run["total"] or 1
        done = run["done"] or 0
        st.progress(min(done / total, 1.0),
                    text=f"Analyzing {done}/{total} games… {run['message'] or ''}")
        st_autorefresh(interval=2000, key=f"refresh_{username}")
    elif run["status"] == "done":
        st.success(f"Analysis complete — {run['message'] or ''}")
    elif run["status"] == "error":
        st.error(f"Analysis failed: {run['message']}")
