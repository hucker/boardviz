"""Review page: big-think analytic, recurring-mistake clusters, mistake browser."""

from __future__ import annotations

import json

import altair as alt
import chess
import chess.svg
import pandas as pd
import streamlit as st

from .. import patterns
from ..analysis_batch import MOVE_TYPE_DEFS, PHASE_DEFS
from ..blitz_analysis import STRUCTURE_DEFS
from . import board as boardui
from . import common

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


# Green won, grey lost on the board, red lost on time — the line to watch.
_CLOCK_COLORS = {"won": "#16a34a", "lost on board": "#6b7280",
                 "lost on time": "#ef4444"}


def _clock_chart(df, opp=None):
    """Line: average clock left (% of base) by move, per game outcome. When ``opp``
    is given, overlay the opponents' overall clock as a dashed reference line."""
    base = (
        alt.Chart(df)
        .mark_line(point=True)
        .encode(
            x=alt.X("move:Q", title="move number"),
            y=alt.Y("pct_remaining:Q", title="clock left (% of base time)"),
            color=alt.Color(
                "outcome_group:N", title="how the game ended",
                scale=alt.Scale(domain=list(_CLOCK_COLORS),
                                range=list(_CLOCK_COLORS.values()))),
            tooltip=["outcome_group", "move", "pct_remaining", "n"],
        )
        .properties(height=320)
    )
    if opp is None or opp.empty:
        return base
    opp_line = (
        alt.Chart(opp.assign(line="opponents (avg)"))
        .mark_line(strokeDash=[6, 4], color="#334155")
        .encode(x="move:Q", y="pct_remaining:Q",
                tooltip=[alt.Tooltip("line:N", title=""), "move",
                         "pct_remaining", "n"])
    )
    return base + opp_line


def _review_body(conn, gf: dict, *, is_me: int, who: str, compare: bool) -> None:
    st.subheader("Where your clock goes")
    st.caption(
        "Average clock left (% of the base time) by move number, split by how the "
        "game ended. If the **lost on time** line falls away early, you're "
        "spending too much too soon and flagging later — that's your cue to move "
        "faster in the calm phase.")
    ct = patterns.clock_trajectory(conn, gf, is_me=is_me)
    if ct.empty:
        st.info("No timed games with a recorded clock for this filter.")
    else:
        # When comparing, overlay the opponents' average clock as a dashed line.
        opp = patterns.clock_by_move(conn, gf, is_me=1 - is_me) if compare else None
        st.altair_chart(_clock_chart(ct, opp), theme="streamlit")
        if opp is not None and not opp.empty:
            st.caption("Dashed line = the other side's average clock (all games).")

    _move_time_view(conn, gf, is_me=is_me)

    st.divider()
    st.subheader("Recurring mistakes")
    eco_names = patterns.eco_opening_names(conn)
    tabs = st.tabs(["By structure", "By move type", "By phase", "By opening"])
    for tab, dim in zip(tabs, ["structure", "move_type", "phase", "eco"]):
        with tab:
            if compare:
                _cluster_compare(conn, gf, dim, is_me=is_me, eco_names=eco_names)
            else:
                _cluster_table(conn, gf, dim, is_me=is_me, eco_names=eco_names)

    st.divider()
    _mistake_browser(conn, gf, is_me=is_me)


def _move_time_hist_chart(hist, median_s: float | None, cap_s: float = 20.0):
    """Bar histogram of seconds-per-move, with a dashed median rule."""
    last = f"{cap_s:g}+"
    bar = (
        alt.Chart(hist)
        .mark_bar(color="#2563eb")
        .encode(
            x=alt.X("bin_start:Q", title="seconds spent on a move",
                    scale=alt.Scale(domainMax=cap_s + 1),
                    axis=alt.Axis(values=[0, 5, 10, 15, cap_s],
                                  labelExpr=f"datum.value == {cap_s} ? '{last}' "
                                            ": datum.value")),
            y=alt.Y("moves:Q", title="moves"),
            tooltip=["bin_start", "moves"],
        )
        .properties(height=240)
    )
    if median_s is None:
        return bar
    rule = (
        alt.Chart(pd.DataFrame({"median_s": [median_s]}))
        .mark_rule(color="#ef4444", strokeDash=[5, 4])
        .encode(x="median_s:Q", tooltip=[alt.Tooltip("median_s:Q", title="median")])
    )
    return bar + rule


def _move_time_view(conn, gf: dict, *, is_me: int) -> None:
    """The move-time distribution (skew) plus median pace by outcome (REV-THINK)."""
    hist = patterns.move_time_hist(conn, gf, is_me=is_me)
    if hist.empty:
        return
    st.divider()
    st.subheader("How you spend time per move")
    pace = patterns.move_time_pace(conn, gf, is_me=is_me)
    median_all = None
    if not pace.empty:
        # Weighted overall median stand-in: total-moves-weighted mean of the medians
        # is close enough for the rule; use the games-you-won median as the anchor.
        won = pace[pace["outcome_group"] == "won"]["median_s"]
        median_all = float(won.iloc[0]) if not won.empty else None
    st.caption(
        "Move-times are heavily right-skewed — most moves are near-instant, a few "
        "are long thinks — so an average misleads. The red line marks your median "
        "(typical) move.")
    st.altair_chart(_move_time_hist_chart(hist, median_all), theme="streamlit")
    if not pace.empty:
        st.markdown("**Pace vs the big thinks, by how the game ended**")
        st.caption(
            "If flag losses show a higher **median** but a similar **p95**, you "
            "flag from a broadly slower tempo — not one catastrophic think.")
        st.dataframe(
            pace, hide_index=True, width="stretch",
            column_config={
                "outcome_group": st.column_config.TextColumn("game ended"),
                "median_s": st.column_config.NumberColumn(
                    "median s/move", help="Your everyday pace (half your moves "
                    "are faster than this)."),
                "p95_s": st.column_config.NumberColumn(
                    "p95 s/move", help="Your long thinks — the slowest 5% of "
                    "moves start here."),
                "moves": st.column_config.NumberColumn("moves"),
            })


def _cluster_compare(conn, gf: dict, dim: str, *, is_me: int,
                     eco_names: dict) -> None:
    """Paired bars per group: the primary side's mistake count vs the other's.

    A fair peer benchmark — both sides play the same games, so per structure /
    opening / phase this shows where you err more (or less) than your opponents.
    """
    primary = patterns.consistent_mistakes(conn, by=dim, game_filter=gf, is_me=is_me)
    other = patterns.consistent_mistakes(
        conn, by=dim, game_filter=gf, is_me=1 - is_me)
    if primary.empty and other.empty:
        st.info("No mistakes for this filter yet.")
        return
    p_lbl, o_lbl = ("you", "opponents") if is_me == 1 else ("opponents", "you")
    label = _DIMS[dim][0]

    def _rows(df: pd.DataFrame, side: str) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame(columns=["group", "side", "mistakes"])
        group = (df[dim].map(eco_names).fillna(df[dim]) if dim == "eco"
                 else df[dim])
        return pd.DataFrame({"group": group, "side": side, "mistakes": df["count"]})

    both = pd.concat([_rows(primary, p_lbl), _rows(other, o_lbl)],
                     ignore_index=True)
    top = both.groupby("group")["mistakes"].sum().nlargest(12).index
    both = both[both["group"].isin(top)]
    chart = (
        alt.Chart(both)
        .mark_bar()
        .encode(
            y=alt.Y("group:N", sort="-x", title=label),
            x=alt.X("mistakes:Q", title="confirmed mistakes"),
            yOffset=alt.YOffset("side:N"),
            color=alt.Color("side:N", title="",
                            scale=alt.Scale(domain=[p_lbl, o_lbl],
                                            range=["#2563eb", "#9ca3af"])),
            tooltip=["group", "side", "mistakes"],
        )
        .properties(height=min(30 * both["group"].nunique() + 40, 440))
    )
    st.altair_chart(chart, theme="streamlit")
    st.caption("Bars are mistake counts over the same games — longer = more "
               "mistakes in that group. Uncheck **Compare** for per-group detail "
               "and example links.")


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
            help="Open the top example game for this group on chess.com "
                 "(opens in a new tab)."),
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
    who = common.profile_picker(conn)
    if who is None:
        st.info("No data yet — import and analyze some games first.")
        return
    gf = common.game_filter_sidebar(conn, key="review", username=who)
    labels = [f"{who}'s" if who else "This profile's", "Their opponents'"]
    side = st.sidebar.radio("Whose mistakes", labels, index=0)
    is_me = 1 if side == labels[0] else 0
    compare = st.sidebar.checkbox(
        "Compare to opponents", value=False,
        help="Show your opponents' numbers beside yours — the clock chart gains a "
        "dashed opponent line, and each mistake cluster becomes paired bars (you "
        "vs opponents). A fair peer benchmark: you both play the same games.")
    _review_body(conn, gf, is_me=is_me, who=who, compare=compare)
