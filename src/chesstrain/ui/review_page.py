"""Review page: big-think analytic, recurring-mistake clusters, mistake browser."""

from __future__ import annotations

import json

import altair as alt
import chess
import chess.svg
import streamlit as st

from .. import patterns
from ..analysis_batch import GAME_STATE_DEFS, MOVE_TYPE_DEFS, PHASE_DEFS
from ..blitz_analysis import STRUCTURE_DEFS
from . import board as boardui
from . import common

_STATE_ORDER = ["winning", "equal", "losing"]

# Per-dimension display label + the glossary that explains its values.
_DIMS = {
    "structure": ("center structure", STRUCTURE_DEFS),
    "move_type": ("move type", MOVE_TYPE_DEFS),
    "phase": ("game phase", PHASE_DEFS),
    "eco": ("opening (ECO)", None),  # ECO has too many codes for a fixed list
}

# Column-header tooltips (st.column_config help=...).
_COL_HELP = {
    "count": "Number of confirmed mistakes in this group (not moves).",
    "median_drop": "Typical eval thrown away per mistake — the MEDIAN centipawns "
                   "lost (100 cp ≈ 1 pawn). Median, not mean, because a blunder "
                   "into forced mate is clamped near 3000 cp and skews an average.",
    "worst_drop": "Largest single eval drop in this group. ~3000 cp means a "
                  "blunder straight into a forced mate.",
    "structure": "Center pawn structure when the mistake was made.",
    "move_type": "What kind of move the mistake was "
                 "(priority: capture > check > retreat > quiet).",
    "phase": "Stage of the game the mistake happened in.",
    "eco": "Encyclopedia of Chess Openings code — a standard opening ID "
           "(e.g. C20). See the opening column for its name.",
}


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
        st.altair_chart(_bigthink_chart(bt), theme="streamlit")
        with st.expander("ℹ️ What do winning / equal / losing mean?"):
            for k, v in GAME_STATE_DEFS.items():
                st.markdown(f"- **{k}** — {v}")

    st.divider()
    st.subheader("Recurring mistakes")
    eco_names = patterns.eco_opening_names(conn)
    tabs = st.tabs(["By structure", "By move type", "By phase", "By opening"])
    for tab, dim in zip(tabs, ["structure", "move_type", "phase", "eco"]):
        with tab:
            _cluster_table(conn, gf, dim, is_me=is_me, eco_names=eco_names)

    st.divider()
    _mistake_browser(conn, gf, is_me=is_me)


def _cluster_table(conn, gf: dict, dim: str, *, is_me: int,
                   eco_names: dict) -> None:
    """One recurring-mistake cluster table with tooltips, links, and a glossary."""
    cm = patterns.consistent_mistakes(conn, by=dim, game_filter=gf, is_me=is_me)
    if cm.empty:
        st.info("No mistakes for this filter yet.")
        return

    disp = cm.copy()
    disp["top_game"] = disp["sample_urls"].apply(lambda u: u[0] if u else None)
    label, glossary = _DIMS[dim]
    colcfg: dict = {
        dim: st.column_config.TextColumn(label, help=_COL_HELP.get(dim)),
        "count": st.column_config.NumberColumn(
            "mistakes", help=_COL_HELP["count"]),
        "median_drop": st.column_config.NumberColumn(
            "typical drop (cp)", help=_COL_HELP["median_drop"]),
        "worst_drop": st.column_config.NumberColumn(
            "worst (cp)", help=_COL_HELP["worst_drop"]),
        "top_game": st.column_config.LinkColumn(
            "example", display_text="open ↗",
            help="Open the top example game for this group."),
        "sample_urls": None,  # hide the raw list; links are surfaced on select
    }
    if dim == "eco":
        disp.insert(1, "opening", disp["eco"].map(eco_names).fillna("—"))
        colcfg["opening"] = st.column_config.TextColumn(
            "opening", help="Opening name resolved from the ECO code.")

    event = st.dataframe(
        disp, hide_index=True, width="stretch", key=f"cm_{dim}",
        on_select="rerun", selection_mode="single-row", column_config=colcfg)

    # Row select -> all example games as clickable links.
    sel = getattr(event, "selection", None)
    picked = list(getattr(sel, "rows", []) or [])
    if picked:
        urls = cm.iloc[picked[0]]["sample_urls"]
        if urls:
            st.markdown("**Example games:** " + "  ·  ".join(
                f"[game {j + 1}]({u})" for j, u in enumerate(urls)))

    if glossary:
        with st.expander(f"ℹ️ What do these {label} values mean?"):
            for k, v in glossary.items():
                st.markdown(f"- **{k}** — {v}")
    elif dim == "eco":
        with st.expander("ℹ️ What is ECO?"):
            st.markdown(
                "**ECO** = *Encyclopedia of Chess Openings*, a standard code "
                "(A00–E99) identifying the opening. The **opening** column "
                "resolves each code to a name from your own games.")


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
