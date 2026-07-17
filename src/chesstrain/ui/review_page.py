"""Review page: big-think analytic, recurring-mistake clusters, mistake browser."""

from __future__ import annotations

import json

import altair as alt
import chess
import chess.svg
import streamlit as st

from .. import patterns
from . import board as boardui
from . import common

_STATE_ORDER = ["winning", "equal", "losing"]


def _bigthink_chart(df):
    """Grouped bar: mistake rate by game state, normal vs long think."""
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("game_state:N", sort=_STATE_ORDER, title="game state"),
            xOffset="think:N",
            y=alt.Y("mistake_rate:Q", title="mistake rate"),
            color=alt.Color("think:N", title=""),
            tooltip=["game_state", "think", "n_moves", "n_mistakes",
                     "mistake_rate", "avg_drop"],
        )
        .properties(height=320)
    )


def _review_body(conn, gf: dict, *, is_me: int, who: str) -> None:
    st.subheader("Big think → mistakes, by game state")
    st.caption(
        "Tests whether long thinks lead to more mistakes — and whether that's "
        "worse when winning. Bars are mistake rate; hover for counts and drop.")
    bt = patterns.bigthink_vs_state(conn, gf, is_me=is_me)
    if bt.empty or bt["n_moves"].sum() == 0:
        st.info("No analyzed moves yet for this filter.")
    else:
        st.altair_chart(_bigthink_chart(bt), use_container_width=True)

    st.divider()
    st.subheader("Recurring mistakes")
    tabs = st.tabs(["By structure", "By move type", "By phase", "By opening"])
    for tab, dim in zip(tabs, ["structure", "move_type", "phase", "eco"]):
        with tab:
            cm = patterns.consistent_mistakes(conn, by=dim, game_filter=gf,
                                              is_me=is_me)
            if cm.empty:
                st.info("No mistakes for this filter yet.")
            else:
                st.dataframe(cm, hide_index=True, use_container_width=True)

    st.divider()
    _mistake_browser(conn, gf, is_me=is_me)


def _mistake_browser(conn, gf: dict, *, is_me: int) -> None:
    st.subheader("Mistake browser")
    df = patterns.mistakes_df(conn, gf, move_is_me=is_me)
    if df.empty:
        st.info("No mistakes to browse.")
        return
    df = df.sort_values("drop_cp", ascending=False).reset_index(drop=True)
    labels = [
        f"#{i}  move {r.fullmove}  {r.structure}/{r.move_type}  −{r.drop_cp}cp"
        for i, r in df.iterrows()
    ]
    idx = st.selectbox("Pick a mistake (worst first)", range(len(labels)),
                       format_func=lambda i: labels[i])
    row = df.iloc[idx]
    board = chess.Board(row["fen"])
    best_pv = json.loads(row["best_pv_json"]) if row["best_pv_json"] else []
    arrows = []
    if best_pv:
        bm = chess.Move.from_uci(best_pv[0])
        arrows.append(chess.svg.Arrow(bm.from_square, bm.to_square, color="#2c7"))
    played = chess.Move.from_uci(row["played_uci"])
    arrows.append(chess.svg.Arrow(played.from_square, played.to_square, color="#c33"))

    c1, c2 = st.columns([1, 1])
    with c1:
        boardui.show_board(board, arrows=arrows, orientation=board.turn)
    with c2:
        st.markdown(f"**Game state:** {row['game_state']}")
        st.markdown(f"**Played** (red): `{row['played_uci']}` — lost {row['drop_cp']}cp")
        if best_pv:
            st.markdown(f"**Best** (green): `{best_pv[0]}`")
            st.markdown("**Best line:** " + " ".join(best_pv))
        if row["url"]:
            st.markdown(f"[Open game]({row['url']})")


def render() -> None:
    st.header("🔍 Review")
    conn = common.get_conn()
    if not common.list_profiles(conn):
        st.info("No data yet — import and analyze some games first.")
        return
    gf = common.game_filter_sidebar(conn, key="review")
    side = st.sidebar.radio("Whose mistakes", ["Me", "Opponent"], index=0)
    is_me = 1 if side == "Me" else 0
    _review_body(conn, gf, is_me=is_me, who=gf.get("username", ""))
