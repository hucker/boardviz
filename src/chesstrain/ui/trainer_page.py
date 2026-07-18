"""Trainer page: drill your mistake positions with a timed +2..-2 score.

Each position runs in three beats: a **preview** (the opponent's piece is
highlighted, you press Start), a **replay** (their move plays at ~their real
pace with a progress bar), then the **puzzle** (the clock starts). Think time is
measured in the browser and returned with the move, so the replay isn't counted
and reruns can't corrupt it. Afterwards you can click through the plus-scoring
alternatives to compare them. The score combines the cached eval grade with the
per-time-control penalty curve.
"""

from __future__ import annotations

import chess
import chess.svg
import streamlit as st

from .. import grading, trainer
from ..analysis_batch import MOVE_TYPE_DEFS, PHASE_DEFS
from ..blitz_analysis import STRUCTURE_DEFS
from . import board as boardui
from . import common

_MODES = {
    "My mistakes (random)": "my_mistakes",
    "Worst mistakes first": "worst",
    "Repeat my misses": "repeat_failures",
}
_GRADE_WORD = {2: "Best", 1: "OK", 0: "Meh", -1: "Inaccuracy", -2: "Blunder"}
_BOARD_SIZE = 600  # match the interactive board so it doesn't resize between beats


def _new_queue(conn, **filt) -> None:
    """Build a fresh drill queue from the sidebar filters (see render)."""
    positions = trainer.select_positions(conn, **filt)
    st.session_state.trainer = {
        "queue": positions, "i": 0, "result": None,
        "started": False, "review_move": None, "total": 0, "answered": 0,
    }


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
        "delayMs": int(min(max(secs, 0.5), 8.0) * 1000),  # clamp 0.5–8s
    }


def _score_line(final: int) -> None:
    color = {2: "🟢", 1: "🟩", -1: "🟧", -2: "🟥"}.get(final, "⬜")
    st.markdown(f"### {color}  Score: {final:+d}")


def _preview(pos: dict, board: chess.Board, state: dict, left, right) -> None:
    """Show the position before the opponent's move + a Start button."""
    prev = chess.Board(pos["prev_epd"] + " 0 1")
    opp_from = chess.parse_square(pos["opp_move"][:2])
    with left:
        boardui.show_board(prev, size=_BOARD_SIZE, fill={opp_from: "#f6d02f"},
                           orientation=board.turn)
        st.caption("Your opponent is about to move (highlighted square).")
    with right:
        st.caption("Press **Start** — their move replays at their real pace, "
                   "then the clock starts and it's your turn.")
        if st.button("▶ Start", type="primary", key=f"start-{state['i']}"):
            state["started"] = True
            st.rerun()


def _puzzle(conn, pos: dict, board: chess.Board, state: dict, left, right) -> None:
    """The live, interactive puzzle: play your move; the clock is client-side."""
    turn = "White" if board.turn else "Black"
    with left:
        played = boardui.board_input(
            board, key=f"trainer-board-{state['i']}", intro=_intro_for(pos))
        st.caption(f"{turn} to move — make your move on the board.")
    with right:
        st.caption("Play the move you think is best — no hints.")
    if not played:
        return
    move = chess.Move.from_uci(played["uci"])
    if move not in board.legal_moves:
        return
    elapsed = played["ms"] / 1000.0  # browser-measured think time
    scored = grading.score_attempt(
        pos["grades"], played["uci"], elapsed, pos["tc_class"])
    scored.update(uci=played["uci"], san=board.san(move), elapsed=elapsed)
    state["result"] = scored
    state["total"] = state.get("total", 0) + scored["final_score"]
    state["answered"] = state.get("answered", 0) + 1
    trainer.record_attempt(
        conn, epd=pos["epd"], source="trainer", played_uci=played["uci"],
        grade=scored["grade"], elapsed_s=elapsed,
        time_penalty=scored["time_penalty"], final_score=scored["final_score"],
        tc_class=pos["tc_class"])
    st.rerun()


def _review(pos: dict, board: chess.Board, state: dict, res: dict,
            left, right) -> None:
    """Answered: your move (always red) + a highlighted alternative to compare."""
    grades = pos["grades"]
    best, played = pos["best_uci"], res["uci"]
    plus = sorted(((u, g) for u, g in grades.items() if g >= 1),
                  key=lambda ug: (-ug[1], ug[0]))
    sel = state.get("review_move") or best

    def _color(uci: str) -> str:  # good move green, mistake red
        return "#2c7" if grades.get(uci, -2) >= 1 else "#c33"

    arrows = []
    if sel != played:  # the alternative you're inspecting
        sm = chess.Move.from_uci(sel)
        arrows.append(chess.svg.Arrow(sm.from_square, sm.to_square,
                                      color=_color(sel)))
    pm = chess.Move.from_uci(played)  # your move, drawn on top
    arrows.append(chess.svg.Arrow(pm.from_square, pm.to_square,
                                  color=_color(played)))

    with left:
        boardui.show_board(board, size=_BOARD_SIZE, arrows=arrows,
                           orientation=board.turn)
        st.caption("Green = a good move, red = a mistake — your move is on top.")
    with right:
        _score_line(res["final_score"])
        st.write(f"Eval grade: **{res['grade']:+d}**  ·  time penalty: "
                 f"**{res['time_penalty']:+d}**  ·  took **{res['elapsed']:.1f}s**")
        st.write(grading.win_loss_readout(pos["eval_cp"]))

        st.caption("Good options — click one to see it on the board:")
        shown = {u for u, _ in plus}
        options = list(plus)
        if played not in shown:  # include your move even if it wasn't a good one
            options.append((played, grades.get(played, 0)))
        for u, g in options:
            word = "Best" if u == best else _GRADE_WORD.get(g, f"{g:+d}")
            tag = "  ← you" if u == played else ""
            mark = "▶ " if u == sel else ""
            san = board.san(chess.Move.from_uci(u))
            if st.button(f"{mark}{word} {g:+d} — {san}{tag}",
                         key=f"opt-{state['i']}-{u}"):
                state["review_move"] = u
                st.rerun()

        if st.button("Next ▶", type="primary", key=f"next-{state['i']}"):
            state["i"] += 1
            state["result"] = None
            state["review_move"] = None
            state["started"] = False
            st.rerun()


def render() -> None:
    st.header("🎯 Trainer")
    conn = common.get_conn()
    profiles = common.list_profiles(conn, is_me=1)
    if not profiles:
        st.info("Analyze some of your games first — the trainer drills your "
                "own mistake positions.")
        return

    def _pick(label, values):
        choice = st.selectbox(label, ["(any)"] + list(values))
        return None if choice == "(any)" else choice

    with st.sidebar:
        st.subheader("Drill setup")
        username = st.selectbox("Profile", profiles)
        mode_label = st.selectbox("Mode", list(_MODES))
        tc = st.selectbox("Time control", ["(all)"] + common.TC_CLASSES)
        st.caption("Pattern — drill a recurring type of mistake:")
        structure = _pick("Structure", STRUCTURE_DEFS)
        move_type = _pick("Move type", MOVE_TYPE_DEFS)
        phase = _pick("Phase", PHASE_DEFS)
        count = st.selectbox("Puzzles", [20, 40], index=0)
        repeated = st.checkbox(
            "Only mistakes I've made before",
            help="Positions you blundered 2+ times across your games — the same "
                 "mistake, made again.")
        filt = dict(
            n=count, mode=_MODES[mode_label], username=username,
            tc_class=None if tc == "(all)" else tc,
            structure=structure, move_type=move_type, phase=phase,
            repeated_only=repeated)
        if st.button("Start / restart drill", type="primary"):
            _new_queue(conn, **filt)

    state = st.session_state.get("trainer")
    if not state or not state["queue"]:
        st.info("Configure a drill in the sidebar and press **Start**. "
                "If nothing loads, you have no graded mistakes for that filter yet.")
        return

    answered = state.get("answered", 0)
    total = state.get("total", 0)
    if answered:
        st.metric("Running score", f"{total:+d}",
                  f"avg {total / answered:+.2f} over {answered}")

    i, queue = state["i"], state["queue"]
    if i >= len(queue):
        st.success(f"Drill complete — {len(queue)} positions, "
                   f"score {total:+d} (avg {total / answered:+.2f})."
                   if answered else f"Drill complete — {len(queue)} positions.")
        if st.button("🔀 New random drill", type="primary"):
            _new_queue(conn, **filt)
            st.rerun()
        return

    pos = queue[i]
    board = chess.Board(pos["fen"])
    st.caption(f"Position {i + 1} / {len(queue)} — "
               f"{pos['structure']} · {pos['move_type']} · {pos['phase']} · "
               f"{pos['tc_class']}")

    res = state["result"]
    left, right = st.columns([3, 2])
    if res is not None:
        _review(pos, board, state, res, left, right)
    elif _intro_for(pos) is not None and not state.get("started"):
        _preview(pos, board, state, left, right)
    else:
        _puzzle(conn, pos, board, state, left, right)
