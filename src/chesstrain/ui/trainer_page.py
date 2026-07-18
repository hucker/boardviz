"""Trainer page: drill your mistake positions with a timed +2..-2 score.

The board component replays the opponent's move (at roughly their real think
time) for context, then starts the clock. Think time is measured in the browser
and returned with the move, so the intro replay isn't counted and reruns can't
corrupt it. The score combines the cached eval grade with the per-time-control
penalty curve.
"""

from __future__ import annotations

import chess
import chess.svg
import streamlit as st

from .. import grading, trainer
from . import board as boardui
from . import common

_MODES = {
    "My mistakes (worst first)": "my_mistakes",
    "Repeat my misses": "repeat_failures",
    "By structure": "by_structure",
}


def _new_queue(conn, mode: str, username: str | None, tc: str | None,
               structure: str | None) -> None:
    positions = trainer.select_positions(
        conn, n=20, mode=mode, username=username, tc_class=tc,
        structure=structure)
    st.session_state.trainer = {"queue": positions, "i": 0, "result": None}


def _intro_for(pos: dict) -> dict | None:
    """Build the pre-puzzle replay: the opponent's move at ~their real pace.

    None when the mistake was the game's first move (no prior ply to replay).
    """
    if not pos.get("prev_epd") or not pos.get("opp_move"):
        return None
    secs = pos.get("opp_seconds") or 1.0
    return {
        "prevFen": pos["prev_epd"] + " 0 1",  # EPD -> FEN (counters don't matter)
        "move": pos["opp_move"],
        "delayMs": int(min(max(secs, 0.5), 5.0) * 1000),  # clamp 0.5–5s
    }


def _score_line(final: int) -> None:
    color = {2: "🟢", 1: "🟩", -1: "🟧", -2: "🟥"}.get(final, "⬜")
    st.markdown(f"### {color}  Score: {final:+d}")


def render() -> None:
    st.header("🎯 Trainer")
    conn = common.get_conn()
    profiles = common.list_profiles(conn, is_me=1)
    if not profiles:
        st.info("Analyze some of your games first — the trainer drills your "
                "own mistake positions.")
        return

    with st.sidebar:
        st.subheader("Drill setup")
        username = st.selectbox("Profile", profiles)
        mode_label = st.selectbox("Mode", list(_MODES))
        tc = st.selectbox("Time control", ["(all)"] + common.TC_CLASSES)
        mode = _MODES[mode_label]
        structure = None
        if mode == "by_structure":
            structure = st.text_input("Structure contains", "open center")
        if st.button("Start / restart drill", type="primary"):
            _new_queue(conn, mode, username,
                       None if tc == "(all)" else tc, structure)

    state = st.session_state.get("trainer")
    if not state or not state["queue"]:
        st.info("Configure a drill in the sidebar and press **Start**. "
                "If nothing loads, you have no graded mistakes for that filter yet.")
        return

    i, queue = state["i"], state["queue"]
    if i >= len(queue):
        st.success("Drill complete! Restart from the sidebar.")
        return

    pos = queue[i]
    board = chess.Board(pos["fen"])
    st.caption(f"Position {i + 1} / {len(queue)} — "
               f"{pos['structure']} · {pos['move_type']} · {pos['phase']} · "
               f"{pos['tc_class']}")

    res = state["result"]
    # Arrows only after answering: your move (red) + best (green).
    arrows = []
    if res is not None:
        bm = chess.Move.from_uci(pos["best_uci"])
        arrows.append(chess.svg.Arrow(bm.from_square, bm.to_square, color="#2c7"))
        ym = chess.Move.from_uci(res["uci"])
        arrows.append(chess.svg.Arrow(ym.from_square, ym.to_square, color="#c33"))

    left, right = st.columns([1, 1])
    turn = "White" if board.turn else "Black"
    if res is None:
        # Unanswered: play the move on the board itself — no move list, no hint
        # about which piece to touch. That's the point of the drill.
        with left:
            played = boardui.board_input(
                board, key=f"trainer-board-{i}", intro=_intro_for(pos))
            st.caption(f"{turn} to move — make your move on the board.")
        with right:
            st.caption("The opponent's move replays first for context, then the "
                       "clock starts. Play the move you think is best — no hints.")
        if played:
            move = chess.Move.from_uci(played["uci"])
            if move in board.legal_moves:
                elapsed = played["ms"] / 1000.0  # browser-measured think time
                scored = grading.score_attempt(
                    pos["grades"], played["uci"], elapsed, pos["tc_class"])
                scored.update(uci=played["uci"], san=board.san(move),
                              elapsed=elapsed)
                state["result"] = scored
                trainer.record_attempt(
                    conn, epd=pos["epd"], source="trainer",
                    played_uci=played["uci"], grade=scored["grade"],
                    elapsed_s=elapsed, time_penalty=scored["time_penalty"],
                    final_score=scored["final_score"], tc_class=pos["tc_class"])
                st.rerun()
    else:
        with left:
            boardui.show_board(board, arrows=arrows, orientation=board.turn)
            st.caption(f"{turn} to move.")
        with right:
            _score_line(res["final_score"])
            st.write(f"Eval grade: **{res['grade']:+d}**  ·  "
                     f"time penalty: **{res['time_penalty']:+d}**  ·  "
                     f"took **{res['elapsed']:.1f}s**")
            st.write(f"You played `{res['uci']}`. Best is `{pos['best_uci']}`.")
            st.write(grading.win_loss_readout(pos["eval_cp"]))
            if st.button("Next ▶", type="primary"):
                state["i"] += 1
                state["result"] = None
                st.rerun()
