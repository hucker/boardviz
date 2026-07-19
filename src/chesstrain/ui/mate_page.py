"""Mate review: how often you finish a forced mate, by distance and motif.

Reads the precomputed ``mate_chances`` table (see ``chesstrain.mate`` and the
analysis pass) — no engine at view time. A diverging chart shows conversion by
distance, and a clickable grid opens each chance on a board with the key move.
"""

from __future__ import annotations

import datetime as dt
import json

import altair as alt
import chess
import chess.svg
import pandas as pd
import streamlit as st

from .. import patterns
from . import board as boardui
from . import common

# Finished (delivered/held the mate) vs blown, validated against both surfaces.
_COLORS = {
    "light": {"finished": "#0a9f4f", "blown": "#d03b3b"},
    "dark": {"finished": "#17b061", "blown": "#e34948"},
}
_SURFACE = {"light": "#fcfcfb", "dark": "#1a1a19"}
_INK = {"light": "#0b0b0b", "dark": "#e8e8e2"}


def _theme_mode() -> str:
    """'dark' or 'light' for the active Streamlit theme (defaults light)."""
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def _fmt_clock(seconds: float | None) -> str:
    """A remaining clock as m:ss (or s.s under 10s); em dash when unknown."""
    if seconds is None or pd.isna(seconds):
        return "—"
    if seconds < 10:
        return f"{seconds:.1f}s"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def _conversion_chart(conv: list[dict], title: str) -> None:
    """Diverging bars: converted (green, right) vs blown (red, left) per row.

    Rows are labelled by ``conv[i]["label"]`` — mate distance or motif — so the
    same chart serves both breakdowns.
    """
    rows = []
    for c in conv:
        rows.append({"m": c["label"], "side": "finished", "x": 0,
                     "x2": c["converted"], "count": c["converted"]})
        rows.append({"m": c["label"], "side": "blown", "x": 0,
                     "x2": -c["missed"], "count": c["missed"]})
    pdf = pd.DataFrame(rows)
    order = [c["label"] for c in conv]
    maxc = max(int(pdf["x2"].abs().max()), 1)
    gutter = max(2, round(maxc * 0.32))  # right margin holding the finish-% column

    mode = _theme_mode()
    colors = _COLORS[mode]
    scale = alt.Scale(domain=list(colors), range=list(colors.values()))
    y = alt.Y("m:N", sort=order, title=None)
    bars = alt.Chart(pdf).mark_bar(stroke=_SURFACE[mode], strokeWidth=1).encode(
        x=alt.X("x:Q", title="←  blown      ·      finished  →",
                scale=alt.Scale(domain=[-(maxc + 1), maxc + gutter]),
                axis=alt.Axis(labelExpr="abs(datum.value)", grid=False)),
        x2="x2:Q", y=y,
        color=alt.Color("side:N", scale=scale, legend=alt.Legend(title=None)),
        tooltip=[alt.Tooltip("m:N", title="Row"),
                 alt.Tooltip("side:N", title="Result"),
                 alt.Tooltip("count:Q", title="Chances")],
    )
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(
        color=_INK[mode], opacity=0.35).encode(x="x:Q")
    # Finish-% as a bold value column in the right gutter, past the longest bar.
    labels = pd.DataFrame(
        [{"m": c["label"], "pct": f"{c['pct']}%", "x": maxc + gutter / 2}
         for c in conv])
    pct = alt.Chart(labels).mark_text(
        align="center", fontWeight="bold", fill=_INK[mode]).encode(
        x=alt.X("x:Q"), y=y, text="pct:N")

    st.subheader(title)
    st.altair_chart(
        (bars + zero + pct).properties(height=min(56 * len(order), 480)),
        theme="streamlit")


def _board_detail(row: pd.Series) -> None:
    """Render a selected chance: the board with the key move, and its facts."""
    board = chess.Board(row["fen"])
    arrows = []
    if row["key_uci"]:
        mv = chess.Move.from_uci(row["key_uci"])
        arrows.append(chess.svg.Arrow(mv.from_square, mv.to_square, color="#2c7"))
    line = json.loads(row["mate_pv_json"]) if row["mate_pv_json"] else []

    left, right = st.columns([1, 1])
    with left:
        boardui.show_board(board, arrows=arrows, orientation=board.turn)
    with right:
        st.markdown(f"**Mate in {row['distance']}** · {row['motif']}")
        st.markdown("**Finished** ✓" if row["converted"]
                    else "**Blown** ✗ — you let the mate slip")
        if row["key_uci"]:
            st.markdown(f"**Key move** (green): `{row['key_uci']}`")
        if line:
            st.markdown("**Forced line:** " + " ".join(line))
        if row["url"]:
            st.markdown(f"[Open game on chess.com]({row['url']})")


def render() -> None:
    st.header("♟️ Mate review")
    conn = common.get_conn()
    if not common.list_profiles(conn):
        st.info("No data yet — import and analyze some games first.")
        return

    gf = common.game_filter_sidebar(conn, key="mate")
    conv = patterns.mate_conversion_by_distance(conn, gf)
    if not conv:
        st.info("No forced mates found for these filters. Analyze some games, "
                "or widen the filters.")
        return

    total = sum(c["chances"] for c in conv)
    finished = sum(c["converted"] for c in conv)
    worst = max(conv, key=lambda c: c["missed"])
    cols = st.columns(3)
    cols[0].metric("Mate chances", total)
    cols[1].metric("Finished", f"{round(100 * finished / total)}%",
                   help="Share of your forced mates you delivered or held to the end.")
    cols[2].metric("Biggest leak", f"{worst['label']}: {worst['missed']} blown")

    _conversion_chart(conv, "Mate conversion by distance")

    by_motif = patterns.mate_conversion_by_motif(conn, gf)
    if by_motif:
        _conversion_chart(by_motif, "Mate conversion by motif")

    df = patterns.mate_chances_df(conn, gf)
    result = st.pills("Result", ["finished", "blown"], selection_mode="multi",
                      key="mate_result",
                      help="Show only mates you finished or blew — pick 'blown' "
                           "to review your fails. Empty = both.")
    if result:
        df = df[df["converted"].isin([1 if r == "finished" else 0 for r in result])]
    motifs = sorted(df["motif"].dropna().unique())
    chosen = st.pills("Motif", motifs, selection_mode="multi", key="mate_motif",
                      help="Filter the positions below by mate pattern. Empty = all.")
    if chosen:
        df = df[df["motif"].isin(chosen)]
    if df.empty:
        st.info("No chances match these filters.")
        return

    disp = pd.DataFrame({
        "distance": df["distance"].map(lambda d: f"M{d}"),
        "motif": df["motif"],
        "result": df["converted"].map({1: "finished", 0: "blown"}),
        "key move": df["key_uci"],
        "clock": df["clock"].map(_fmt_clock),
        "time pressure": df["clock"] < 10,  # NaN clocks compare False
        "date": df["end_time"].map(
            lambda t: dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else ""),
        "game": df["url"],
    })
    st.caption(f"{len(disp)} mate chances — click a row to see the position.")
    event = st.dataframe(
        disp, hide_index=True, width="stretch", key="mate_grid",
        on_select="rerun", selection_mode="single-row",
        column_config={
            "clock": st.column_config.TextColumn(
                "clock", help="Your remaining time when the mate was on the board."),
            "time pressure": st.column_config.CheckboxColumn(
                "⏱ <10s", help="You had under 10 seconds when the mate appeared."),
            "game": st.column_config.LinkColumn(
                "game", display_text="open ↗",
                help="Open this game on chess.com (opens in a new tab).")})
    picked = list(getattr(getattr(event, "selection", None), "rows", []) or [])
    if picked:
        _board_detail(df.iloc[picked[0]])
