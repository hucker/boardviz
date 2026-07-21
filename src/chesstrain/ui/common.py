"""Shared Streamlit helpers: cached DB connection, profile/filter widgets, and
launching the analysis subprocess.
"""

from __future__ import annotations

import subprocess
import sys

import streamlit as st

from .. import db, patterns

TC_CLASSES = ["bullet", "blitz", "rapid", "daily"]
# Stored games.end_method values, most-common first (see db.classify_end_method).
END_METHODS = ["resignation", "checkmate", "on time", "abandoned", "draw", "other"]
# Low-clock-at-end presets -> db.clock_where spec (absolute cutoff, or a fraction
# of the base time control so one setting scales across bullet/blitz/rapid).
CLOCK_PRESETS = {
    "under 5s": {"seconds": 5.0},
    "under 20s": {"seconds": 20.0},
    "under 60s": {"seconds": 60.0},
    "under 10% of base time": {"frac": 0.10},
}


@st.cache_resource
def get_conn():
    """One process-wide connection (WAL; reads see the subprocess's commits)."""
    conn = db.connect()
    db.init_db(conn)
    return conn


def list_profiles(conn) -> list[str]:
    """All imported profiles, the default first (see db.default_profile)."""
    return [
        r["username"]
        for r in conn.execute(
            "SELECT username FROM players ORDER BY is_default DESC, username"
        ).fetchall()
    ]


def profile_index(conn, profiles: list[str]) -> int:
    """Index of the default profile within ``profiles`` (0 if absent) — for
    seeding a selectbox so pages open on the default."""
    default = db.default_profile(conn)
    return profiles.index(default) if default in profiles else 0


def profile_help_text(conn, username: str) -> str:
    """The Player picker's help blurb: the profile's game counts by time control
    (and how many are analyzed) — a quick read on what data it holds."""
    rows = conn.execute(
        "SELECT COALESCE(tc_class, 'other') AS tc, COUNT(*) AS n, "
        "COALESCE(SUM(analyzed), 0) AS a FROM games WHERE username=? GROUP BY tc",
        (username,)).fetchall()
    by_tc = {r["tc"]: r["n"] for r in rows}
    total = sum(by_tc.values())
    analyzed = sum(r["a"] for r in rows)
    order = [*TC_CLASSES, "other"] + [t for t in by_tc if t not in [*TC_CLASSES, "other"]]
    breakdown = " · ".join(f"{tc} {by_tc[tc]}" for tc in order if by_tc.get(tc))
    return ("Whose games you're viewing — the choice follows you across pages.\n\n"
            f"**{username}**: {total} games · {analyzed} analyzed  \n"
            f"{breakdown or 'no games'}")


def profile_picker(conn) -> str | None:
    """A prominent, page-top **Player** selector, shared across screens via one
    session key so the chosen profile follows you between pages. Defaults to the
    default profile; returns ``None`` when no profiles exist yet."""
    profiles = list_profiles(conn)
    if not profiles:
        return None
    if st.session_state.get("active_profile") not in profiles:
        st.session_state["active_profile"] = db.default_profile(conn) or profiles[0]
    active = st.session_state["active_profile"]
    return st.columns([1, 2])[0].selectbox(
        "Player", profiles, key="active_profile",
        help=profile_help_text(conn, active))


def launch_analyze(username: str) -> subprocess.Popen:
    """Start `chesstrain analyze` as a detached subprocess (owns its engine)."""
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW  # no console popup
    return subprocess.Popen(
        [sys.executable, "-m", "chesstrain.cli", "analyze", "--user", username],
        creationflags=creationflags,
    )


def game_filter_sidebar(conn, key: str, username: str) -> dict:
    """Render sidebar game filters and return a filter dict for patterns/db.

    ``username`` is the active profile (chosen by the page-top profile_picker);
    the sidebar holds only the filters. Layout: Recent N is always visible; the
    rest live in collapsible groups so the panel stays short. Each group shows a
    count of its active filters and auto-opens when it has any, so a collapsed
    group can't silently hide a filter that's shaping the data.
    """

    def _prev(suffix: str, default):  # last run's widget value, for the badges
        return st.session_state.get(f"{key}_{suffix}", default)

    def _on(val) -> bool:  # is a filter active? (empty list / "(all)" = off)
        if isinstance(val, (list, tuple)):
            return len(val) > 0
        return val not in (None, "", "(all)")

    n_format = sum(
        _on(_prev(s, d))
        for s, d in (
            ("tc", []),
            ("color", []),
            ("out", []),
            ("end", []),
            ("method", []),
            ("flag", "(all)"),
            ("analyzed", "(all)"),
        )
    )
    n_open = int(_on(_prev("opening", ""))) + int(_on(_prev("eco", [])))
    n_clock = int(_prev("clock", "(any)") not in (None, "(any)")) + int(
        bool(_prev("tt", False))
    )

    def _title(name: str, n: int) -> str:
        return f"{name}  ·  {n} on" if n else name

    with st.sidebar:
        st.subheader("Filters")
        recent_n = st.number_input(
            "Most recent N games (0 = all)",
            min_value=0,
            value=0,
            step=10,
            key=f"{key}_recent",
            help="Scope EVERYTHING — metrics, chart, and table — to your last N "
            "games (e.g. the batch you just downloaded). 0 = all games.",
        )

        with st.expander(_title("Result & format", n_format), expanded=bool(n_format)):
            st.caption("Time control /Color / result: empty = all.")
            tc = st.pills(
                "Time control", TC_CLASSES, selection_mode="multi", key=f"{key}_tc"
            )
            color = st.pills(
                "Color", ["white", "black"], selection_mode="multi", key=f"{key}_color"
            )
            outcome = st.pills(
                "Result",
                ["win", "loss", "draw"],
                selection_mode="multi",
                key=f"{key}_out",
            )
            end_state = st.pills(
                "End state",
                ["winning", "even", "losing"],
                selection_mode="multi",
                key=f"{key}_end",
                help="Your engine eval at the final position — surfaces games "
                "you resigned or lost on time while still winning.",
            )
            end_method = st.pills(
                "How it ended",
                END_METHODS,
                selection_mode="multi",
                key=f"{key}_method",
                format_func=str.capitalize,
                help="Termination method. Pair with End state = winning to find "
                "games you resigned or flagged while ahead.",
            )
            flagged = st.selectbox(
                "Flagged",
                ["(all)", "Flag losses only", "Exclude flag losses"],
                key=f"{key}_flag",
            )
            analysis = st.selectbox(
                "Analysis", ["(all)", "Analyzed", "Not analyzed"], key=f"{key}_analyzed"
            )

        with st.expander(_title("Clock", n_clock), expanded=bool(n_clock)):
            st.caption(
                "Find time scrambles: games that ended with little time "
                "left on the clock."
            )
            low_clock = st.selectbox(
                "Low clock at end",
                ["(any)", *CLOCK_PRESETS],
                key=f"{key}_clock",
                help="Keep only games whose remaining clock at the end was under "
                "this. '10% of base time' scales the cutoff to the game's "
                "time control — ≈18s in a 3-minute blitz, ≈60s in a "
                "10-minute rapid — so one setting works across speeds.",
            )
            clock_who = st.pills(
                "…on whose clock",
                ["me", "opponent"],
                selection_mode="multi",
                key=f"{key}_clockwho",
                help="Whose clock must be low. Empty = either player.",
            )
            time_trouble = st.checkbox(
                "Only time-trouble losses",
                key=f"{key}_tt",
                help="Games you lost to the clock: an actual flag, OR a "
                "resignation with your clock critically low and far behind "
                "your opponent's — you lost the clock race, so resigning "
                "only conceded an imminent flag. Independent of the cutoff "
                "above.",
            )

        with st.expander(_title("Opening", n_open), expanded=bool(n_open)):
            opening = st.text_input(
                "Opening contains", key=f"{key}_opening", placeholder="e.g. French"
            )
            eco_names = patterns.eco_opening_names(conn)
            eco = st.multiselect(
                "Opening (ECO)",
                sorted(eco_names),
                key=f"{key}_eco",
                format_func=lambda c: f"{c} — {eco_names.get(c, '')}",
            )
    gf: dict = {}
    if username:
        gf["username"] = username
        if recent_n:
            cutoff = db.nth_recent_end_time(conn, username, int(recent_n))
            if cutoff is not None:
                gf["min_end_time"] = cutoff
    if tc:
        gf["tc_class"] = tc
    if color:
        gf["my_color"] = color
    if outcome:
        gf["outcome"] = outcome
    if end_state:
        gf["end_state"] = end_state
    if end_method:
        gf["end_method"] = end_method
    if low_clock != "(any)":
        gf["clock"] = {"who": clock_who, **CLOCK_PRESETS[low_clock]}
    if time_trouble:
        gf["time_trouble"] = True
    if opening.strip():
        gf["opening"] = opening.strip()
    if eco:
        gf["eco"] = eco
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
        st.progress(
            min(done / total, 1.0),
            text=f"Analyzing {done}/{total} games… {run['message'] or ''}",
        )
        st_autorefresh(interval=2000, key=f"refresh_{username}")
    elif run["status"] == "done":
        st.success(f"Analysis complete — {run['message'] or ''}")
    elif run["status"] == "error":
        st.error(f"Analysis failed: {run['message']}")
