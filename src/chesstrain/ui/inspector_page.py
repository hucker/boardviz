"""Inspector: paste a FEN, get the engine eval and a winning/losing readout."""

from __future__ import annotations

import chess
import streamlit as st

from .. import config, engine, grading
from . import board as boardui

_START = chess.STARTING_FEN


def render() -> None:
    st.header("🔬 Position inspector")
    st.caption("Paste a FEN to see the engine eval and whether the side to move "
               "is winning or losing by more than your threshold.")

    fen = st.text_input("FEN", value=_START)
    thr = st.slider("Threshold X (pawns)", 0.5, 5.0,
                    config.WIN_THRESHOLD_CP / 100.0, 0.5)
    depth = st.slider("Engine depth", 6, 20, 14)

    try:
        board = chess.Board(fen)
    except ValueError:
        st.error("Invalid FEN.")
        return

    c1, c2 = st.columns([1, 1])
    with c1:
        boardui.show_board(board, orientation=board.turn)
    with c2:
        if st.button("Evaluate", type="primary"):
            try:
                with st.spinner("Analyzing…"):
                    cp = engine.eval_cp(board, depth=depth)
            except config.EngineNotFound as exc:
                st.error(str(exc))
                return
            mover = "White" if board.turn else "Black"
            st.metric(f"Eval ({mover} to move)", f"{cp / 100:+.2f}")
            st.info(grading.win_loss_readout(cp, int(thr * 100), pov=mover))
