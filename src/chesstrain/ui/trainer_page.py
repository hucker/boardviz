"""Trainer page: drill your mistake positions with a timed +2..-2 score.

Each position: a short **bearings** pause (a couple of seconds to look, the
opponent's last move highlighted), then the **puzzle** — the clock starts and you
move. Think time is measured in the browser, so the pause isn't counted. In
**Auto** it flows hands-free (auto-start, auto-advance after the answer); with
Auto off you press Start for each and Next to move on. The score combines the
cached eval grade with the per-time-control penalty curve.
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
_ADVANCE_RIGHT_MS = 500  # got it right in Auto mode: brief flash, then next
_ADVANCE_WRONG_MS = 2000  # missed it: a slower beat (Pause to study longer)
_BEARINGS_MS = 2000  # a beat to read the position before the clock starts


def _new_queue(conn, **filt) -> None:
    """Build a fresh drill queue from the sidebar filters (see render)."""
    positions = trainer.select_positions(conn, **filt)
    drill = st.session_state.get("_drill_n", 0) + 1  # unique per drill, for keys
    st.session_state["_drill_n"] = drill
    st.session_state.trainer = {
        "queue": positions, "i": 0, "result": None, "started": False,
        "review_move": None, "total": 0, "answered": 0, "drill": drill,
    }


def _bearings_for(pos: dict) -> dict:
    """A short 'get your bearings' pause before the clock, highlighting the
    opponent's last move (None if this was the game's first move)."""
    return {"delayMs": _BEARINGS_MS, "lastMove": pos.get("opp_move")}


def _advance(state: dict) -> None:
    """Move the drill to the next position (reset per-position state)."""
    state["i"] += 1
    state["result"] = None
    state["review_move"] = None
    state["started"] = False
    state["paused"] = False


def _score_line(final: float) -> None:
    color = "🟢" if final >= 1 else "🟨" if final >= 0.5 else "🟥"
    label = f"+{final:g}" if final > 0 else "0"
    st.markdown(f"### {color}  Score: {label}")


def _start_gate(pos: dict, board: chess.Board, state: dict, left, right) -> None:
    """Manual mode: show the position with a Start button before the clock."""
    with left:
        boardui.show_board(board, size=_BOARD_SIZE, orientation=board.turn)
        st.caption("Your move to find — press Start when you're ready.")
    with right:
        if st.button("▶ Start", type="primary", key=f"start-{state['i']}"):
            state["started"] = True
            st.rerun()


def _puzzle(conn, pos: dict, board: chess.Board, state: dict, left, right) -> None:
    """The live, interactive puzzle: play your move; the clock is client-side."""
    turn = "White" if board.turn else "Black"
    with left:
        played = boardui.board_input(
            board, key=f"trainer-board-{state['i']}", intro=_bearings_for(pos))
        st.caption(f"{turn} to move — make your move on the board.")
    with right:
        st.caption("Play the move you think is best — no hints.")
    if not played:
        return
    move = chess.Move.from_uci(played["uci"])
    if move not in board.legal_moves:
        return
    elapsed = played["ms"] / 1000.0  # browser-measured think time (recorded, not scored)
    scored = grading.score_attempt(pos["grades"], played["uci"])
    scored.update(uci=played["uci"], san=board.san(move), elapsed=elapsed)
    state["result"] = scored
    state["total"] = state.get("total", 0) + scored["final_score"]
    state["answered"] = state.get("answered", 0) + 1
    trainer.record_attempt(
        conn, epd=pos["epd"], source="trainer", played_uci=played["uci"],
        grade=scored["grade"], elapsed_s=elapsed, time_penalty=0,
        final_score=scored["final_score"], tc_class=pos["tc_class"])
    st.rerun()


def _review(pos: dict, board: chess.Board, state: dict, res: dict,
            left, right, *, auto: bool) -> None:
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
        # Make the move's quality unmissable when you didn't find the best move.
        best_san = board.san(chess.Move.from_uci(best))
        if played == best:
            st.success(f"✓ **Best move** — {res['san']}")
        else:
            word = _GRADE_WORD.get(res["grade"], f"{res['grade']:+d}")
            box = st.error if res["grade"] <= -1 else st.warning
            box(f"Your move **{res['san']}** was **{word}** ({res['grade']:+d}) "
                f"— best was **{best_san}** (+2).")
        st.caption(f"took {res['elapsed']:.1f}s (not scored)")
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

        # Auto keeps cycling — fast when right, a slower beat when wrong — but a
        # Pause stops the timer so you can study a miss for as long as you like.
        got_it = res["grade"] >= 1
        if auto and not state.get("paused"):
            from streamlit_autorefresh import st_autorefresh
            delay = _ADVANCE_RIGHT_MS if got_it else _ADVANCE_WRONG_MS
            st.caption("Correct — next…" if got_it else "Next shortly — Pause to study")
            if st.button("⏸ Pause", key=f"pause-{state['i']}"):
                state["paused"] = True
                st.rerun()
            if st_autorefresh(interval=delay,
                              key=f"auto-{state['drill']}-{state['i']}"):
                _advance(state)
                st.rerun()
        elif st.button("Next ▶", type="primary", key=f"next-{state['i']}"):
            _advance(state)
            st.rerun()


def render() -> None:
    st.header("🎯 Trainer")
    conn = common.get_conn()
    profiles = common.list_profiles(conn, is_me=1)
    if not profiles:
        st.info("Analyze some of your games first — the trainer drills your "
                "own mistake positions.")
        return

    def _pills(label, values):  # multi-select; [] (empty) means all
        return st.pills(label, list(values), selection_mode="multi") or None

    with st.sidebar:
        st.subheader("Drill setup")
        username = st.selectbox("Profile", profiles)
        mode_label = st.selectbox("Mode", list(_MODES))
        auto = st.checkbox(
            "Auto (hands-free)", value=True,
            help="On: each puzzle auto-starts after a ~2s look and auto-advances "
                 "once you answer. Off: press Start for each, Next to move on.")
        tc = _pills("Time control", common.TC_CLASSES)
        st.caption("Pattern — pick any combination; empty = all:")
        structure = _pills("Structure", STRUCTURE_DEFS)
        move_type = _pills("Move type", MOVE_TYPE_DEFS)
        phase = _pills("Phase", PHASE_DEFS)
        opening_like = st.text_input(
            "Opening contains", placeholder="e.g. french advance",
            help="Drill one line — matches any opening whose name contains these "
                 "words. 'french' = all French; 'french advance' = the Advance "
                 "(all variants). Empty = all openings.").strip() or None
        max_fullmove = None
        if opening_like:
            # An opening's character is in its first moves; deeper positions have
            # usually transformed past the structure/theory you're drilling.
            max_fullmove = int(st.number_input(
                "Opening depth — up to move #", min_value=1, max_value=40, value=6,
                help="Only the first N moves, where the opening's structure and "
                     "theory live. Deeper positions have usually transformed."))
        count = st.selectbox("Puzzles", [20, 40], index=0)
        repeated = st.checkbox(
            "Only mistakes I've made before",
            help="Positions you blundered 2+ times across your games — the same "
                 "mistake, made again.")
        filt = dict(
            n=count, mode=_MODES[mode_label], username=username,
            tc_class=tc, structure=structure, move_type=move_type, phase=phase,
            opening_like=opening_like, max_fullmove=max_fullmove,
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
        st.metric("Running score", f"{total:g} / {answered}",
                  f"avg {total / answered:.2f}")

    i, queue = state["i"], state["queue"]
    if i >= len(queue):
        st.success(f"Drill complete — {total:g} / {len(queue)} "
                   f"(avg {total / answered:.2f})."
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
        _review(pos, board, state, res, left, right, auto=auto)
    elif not auto and not state.get("started"):
        _start_gate(pos, board, state, left, right)  # manual: wait for Start
    else:
        _puzzle(conn, pos, board, state, left, right)
