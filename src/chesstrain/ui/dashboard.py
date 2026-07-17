"""Dashboard: filtered summary and game list."""

from __future__ import annotations

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

from .. import db, patterns
from . import common

# Win / draw / loss slice colors, validated (dataviz skill) against both chart
# surfaces. Green↔gray sits in the CVD "needs secondary encoding" band, which
# the legend, 2px slice gaps, and direct method labels supply.
_OUTCOME_COLORS = {
    "light": {"win": "#0a9f4f", "draw": "#8f8f8a", "loss": "#d03b3b"},
    "dark": {"win": "#17b061", "draw": "#8f8f8a", "loss": "#e34948"},
}
_SURFACE = {"light": "#fcfcfb", "dark": "#1a1a19"}
_INK = {"light": "#0b0b0b", "dark": "#e8e8e2"}


def _theme_mode() -> str:
    """'dark' or 'light' for the active Streamlit theme (defaults light)."""
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def _termination_bars(conn, gf: dict) -> None:
    """Diverging bars of how games end: wins right (green), losses left (red).

    One row per termination method, most frequent on top. Draw has no side, so
    it's a neutral gray bar straddling the zero line.
    """
    breakdown = patterns.termination_breakdown(conn, gf)
    if not breakdown:
        return
    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    draws = 0
    for r in breakdown:
        if r["outcome"] == "draw":
            draws += r["count"]
        elif r["outcome"] == "win":
            wins[r["method"]] = wins.get(r["method"], 0) + r["count"]
        else:
            losses[r["method"]] = losses.get(r["method"], 0) + r["count"]

    methods = sorted(set(wins) | set(losses),
                     key=lambda m: -(wins.get(m, 0) + losses.get(m, 0)))
    rows = []
    for m in methods:
        if wins.get(m):
            rows.append({"method": m.capitalize(), "side": "win",
                         "x": 0, "x2": wins[m], "count": wins[m]})
        if losses.get(m):
            rows.append({"method": m.capitalize(), "side": "loss",
                         "x": 0, "x2": -losses[m], "count": losses[m]})
    if draws:
        rows.append({"method": "Draw", "side": "draw",
                     "x": -draws / 2, "x2": draws / 2, "count": draws})
    if not rows:
        return

    pdf = pd.DataFrame(rows)
    order = [m.capitalize() for m in methods] + (["Draw"] if draws else [])
    span = max(int(pdf[["x", "x2"]].abs().to_numpy().max()), 1) + 1

    mode = _theme_mode()
    colors = _OUTCOME_COLORS[mode]
    scale = alt.Scale(domain=list(colors), range=list(colors.values()))
    y = alt.Y("method:N", sort=order, title=None)
    base = alt.Chart(pdf)
    bars = base.mark_bar(stroke=_SURFACE[mode], strokeWidth=1).encode(
        x=alt.X("x:Q", title="←  losses      ·      wins  →",
                scale=alt.Scale(domain=[-span, span]),
                axis=alt.Axis(labelExpr="abs(datum.value)", grid=False)),
        x2="x2:Q",
        y=y,
        color=alt.Color("side:N", scale=scale,
                        legend=alt.Legend(title="Result")),
        tooltip=[alt.Tooltip("method:N", title="Termination"),
                 alt.Tooltip("side:N", title="Result"),
                 alt.Tooltip("count:Q", title="Games")],
    )
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(
        color=_INK[mode], opacity=0.35).encode(x="x:Q")
    # Count at the end of each bar; sign picks which way the text hangs.
    right = base.transform_filter(alt.datum.x2 > 0).mark_text(
        align="left", dx=4, fill=_INK[mode]).encode(
        x="x2:Q", y=y, text="count:Q")
    left = base.transform_filter(alt.datum.x2 < 0).mark_text(
        align="right", dx=-4, fill=_INK[mode]).encode(
        x="x2:Q", y=y, text="count:Q")

    st.subheader("How games end")
    st.altair_chart(
        (bars + zero + right + left).properties(height=min(64 * len(order), 380)),
        theme="streamlit")


def render() -> None:
    st.header("📊 Dashboard")
    conn = common.get_conn()
    if not common.list_profiles(conn):
        st.info("No data yet — import some games first.")
        return

    gf = common.game_filter_sidebar(conn, key="dash")
    counts = patterns.summary_counts(conn, gf)

    cols = st.columns(5)
    cols[0].metric("Games", counts["games"])
    cols[1].metric("Wins", counts["wins"])
    cols[2].metric("Losses", counts["losses"])
    cols[3].metric("Draws", counts["draws"])
    cols[4].metric("Flag losses", counts["flag_losses"])

    _termination_bars(conn, gf)

    rows = db.query_games(
        conn, username=gf.get("username"), tc_class=gf.get("tc_class"),
        color=gf.get("my_color"), outcome=gf.get("outcome"),
        opening=gf.get("opening"), flagged=gf.get("flagged"),
        analyzed=gf.get("analyzed"))
    if not rows:
        st.info("No games match these filters.")
        return

    df = pd.DataFrame([{
        "date": dt.datetime.fromtimestamp(r["end_time"]).strftime("%Y-%m-%d %H:%M"),
        "color": r["my_color"], "result": r["outcome"], "tc": r["tc_class"],
        "moves": r["n_moves"], "eco": r["eco"], "opening": r["opening"],
        "flagged": bool(r["flagged"]), "analyzed": bool(r["analyzed"]),
        "url": r["url"],
    } for r in rows])
    df["moves"] = df["moves"].astype("Int64")  # nullable int: clean, no 35.0
    moves_note = (f" · avg {df['moves'].dropna().mean():.0f} moves"
                  if df["moves"].notna().any() else "")
    st.caption(f"{len(df)} games{moves_note}")
    st.dataframe(
        df, hide_index=True, width="stretch",
        column_config={"url": st.column_config.LinkColumn("game", display_text="open")},
    )
