"""Shared Streamlit helpers: cached DB connection, profile/filter widgets, and
launching the analysis subprocess.
"""

from __future__ import annotations

import base64
import datetime as dt
import subprocess
import sys

import streamlit as st

from .. import db, patterns

def nav_pages(hosted: bool) -> list:
    """The navigation roster for st.navigation.

    A hosted demo (ENV-HOSTED) lands on the Dashboard, and its Import page is
    visible but inert (the page disables its actions itself); locally, Import
    is the landing page.

    Args:
        hosted: Whether the app runs as a read-only hosted demo.

    Returns:
        The list of st.Page objects for st.navigation.
    """
    # Imported here: the page modules import this module back.
    from . import dashboard, import_page, mate_page, review_page, trainer_page
    return [
        st.Page(import_page.render, title="Import", icon="📥",
                url_path="import", default=not hosted),
        st.Page(dashboard.render, title="Dashboard", icon="📊",
                url_path="dashboard", default=hosted),
        st.Page(review_page.render, title="Review", icon="🔍", url_path="review"),
        st.Page(mate_page.render, title="Mate review", icon="♟️", url_path="mate"),
        st.Page(trainer_page.render, title="Trainer", icon="🎯",
                url_path="trainer"),
    ]


TC_CLASSES = ["bullet", "blitz", "rapid", "daily"]
SOURCES = ["chess.com", "lichess"]
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


def _tc_bar_svg(by_tc: dict[str, int]) -> str:
    """A tiny horizontal bar chart (games per time control) as an SVG string.

    Self-contained (own white background + inline styles) so it reads on any
    tooltip theme; ordered bullet→blitz→rapid→daily then anything else.
    """
    order = [tc for tc in [*TC_CLASSES, "other"] if by_tc.get(tc)]
    order += [tc for tc in by_tc if tc not in order and by_tc.get(tc)]
    if not order:
        return ""
    maxn = max(by_tc[tc] for tc in order)
    rowh, barmax, x0, w = 20, 120, 54, 235
    h = rowh * len(order) + 8
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="sans-serif">',
        f'<rect width="{w}" height="{h}" rx="4" fill="#ffffff"/>',
    ]
    for i, tc in enumerate(order):
        y = 6 + i * rowh
        bw = max(2, round(by_tc[tc] / maxn * barmax))
        parts.append(f'<text x="4" y="{y + 11}" font-size="11" fill="#333">{tc}</text>')
        parts.append(f'<rect x="{x0}" y="{y + 2}" width="{bw}" height="12" rx="2" '
                     f'fill="#4c9be8"/>')
        parts.append(f'<text x="{x0 + bw + 4}" y="{y + 11}" font-size="10" '
                     f'fill="#555">{by_tc[tc]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def profile_help_text(conn, username: str) -> str:
    """The Player picker's help: total/analyzed plus a small bar chart (games by
    time control) embedded as a data-URI SVG image — a quick read on the data."""
    rows = conn.execute(
        "SELECT COALESCE(tc_class, 'other') AS tc, COUNT(*) AS n, "
        "COALESCE(SUM(analyzed), 0) AS a FROM games WHERE username=? GROUP BY tc",
        (username,)).fetchall()
    by_tc = {r["tc"]: r["n"] for r in rows}
    total = sum(by_tc.values())
    analyzed = sum(r["a"] for r in rows)
    head = ("Whose games you're viewing — the choice follows you across pages.\n\n"
            f"**{username}**: {total} games · {analyzed} analyzed")
    span = conn.execute(
        "SELECT MIN(end_time) AS lo, MAX(end_time) AS hi FROM games "
        "WHERE username=? AND end_time IS NOT NULL", (username,)).fetchone()
    if span and span["lo"] is not None:
        lo, hi = dt.datetime.fromtimestamp(span["lo"]), dt.datetime.fromtimestamp(span["hi"])
        head += ("  \n" + (f"{lo:%b %Y}" if (lo.year, lo.month) == (hi.year, hi.month)
                           else f"{lo:%b %Y} – {hi:%b %Y}"))
    svg = _tc_bar_svg(by_tc)
    if not svg:
        return head + "\n\nno games"
    b64 = base64.b64encode(svg.encode()).decode()
    return head + f"\n\n![games by time control](data:image/svg+xml;base64,{b64})"


_FEN_DOC = "https://en.wikipedia.org/wiki/Forsyth%E2%80%93Edwards_Notation"
_EPD_DOC = "https://www.chessprogramming.org/Extended_Position_Description"


def game_source(url: str | None) -> str | None:
    """The site a game came from, read from its URL — 'lichess' or 'chess.com'
    (defaults to chess.com). None when there's no URL. Keeps the app source-aware
    ahead of a lichess importer."""
    if not url:
        return None
    return "lichess" if "lichess" in url else "chess.com"


def game_info_help(conn, *, fen: str, url: str | None = None,
                   epd: str | None = None, tc_class: str | None = None) -> str:
    """A markdown "game info" blob for a ``?`` tooltip: the matchup, date, time
    control and opening (from the game's PGN via :func:`db.game_meta`), a link to
    the game, and the copyable FEN / EPD. Reusable across the trainer, mate and
    review screens. Streamlit renders markdown (incl. clickable links) in help.
    """
    meta = db.game_meta(conn, url)
    lines: list[str] = []
    if meta.get("white") and meta.get("black"):
        lines.append(f"**{meta['white']} vs {meta['black']}**")
    ctx = []
    if meta.get("date"):
        ctx.append(meta["date"].replace(".", "-"))  # 2025.12.25 -> 2025-12-25
    tc = (tc_class or meta.get("tc_class") or "").capitalize()
    if tc:
        ctx.append(tc)
    if meta.get("opening"):
        ctx.append(meta["opening"])
    if ctx:
        lines.append(" · ".join(ctx))
    if url:
        lines.append(f"🌐 [Open on {game_source(url)}]({url})")
    # FEN/EPD labels link out to an explanation of the notation.
    lines.append(f"[**FEN**]({_FEN_DOC}) `{fen}`")
    if epd:
        lines.append(f"[**EPD**]({_EPD_DOC}) `{epd}`")
    return "  \n".join(lines)  # two-space soft breaks keep it compact in the tooltip


def profile_picker(conn, *, allow_new: bool = False) -> str | None:
    """The **Player** selector at the top of the sidebar, shared across screens
    via one session key so the chosen profile follows you between pages. Defaults
    to the default profile. With ``allow_new`` (the Import page) you can also type
    a brand-new chess.com username to fetch. Returns ``None`` only when there is
    no profile and none was typed."""
    profiles = list_profiles(conn)
    cur = st.session_state.get("active_profile")
    # Seed the default when the current pick can't be shown as-is. A typed new
    # username (allow_new) is kept; elsewhere an unknown value resets to default.
    if profiles and cur not in profiles and not (allow_new and cur):
        st.session_state["active_profile"] = db.default_profile(conn) or profiles[0]
    if not profiles and not allow_new:
        return None
    active = st.session_state.get("active_profile")
    return st.sidebar.selectbox(
        "Player", profiles, key="active_profile", accept_new_options=allow_new,
        placeholder="Pick a profile" + (" or type a username" if allow_new else ""),
        help=profile_help_text(conn, active) if active else None)


def launch_analyze(username: str) -> subprocess.Popen:
    """Start `boardviz analyze` as a detached subprocess (owns its engine)."""
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW  # no console popup
    return subprocess.Popen(
        [sys.executable, "-m", "boardviz.cli", "analyze", "--user", username],
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
            ("source", []),
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
            st.caption("Time control / colour / result: empty = all.")
            tc = st.pills(
                "Time control", TC_CLASSES, selection_mode="multi", key=f"{key}_tc"
            )
            source = st.pills(
                "Source", SOURCES, selection_mode="multi", key=f"{key}_source",
                help="Filter by where the games were imported from. Empty = all.",
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
    if source:
        gf["source"] = source
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
