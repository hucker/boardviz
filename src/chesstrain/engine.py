"""Stockfish process lifecycle.

Two engines that are never shared:

* **Batch** engine (:func:`get_batch_engine`) — a fresh, configured
  ``SimpleEngine`` the CLI analysis subprocess owns and quits itself. It never
  touches Streamlit.
* **Interactive** engine (:func:`get_interactive_engine`) — one long-lived
  instance cached across Streamlit reruns via ``st.cache_resource`` and guarded
  by :data:`ENGINE_LOCK`, because ``SimpleEngine`` is synchronous and a second
  concurrent ``analyse`` corrupts the UCI stream. Used only for cheap on-demand
  reads (the win/loss readout, ad-hoc FEN inspection).

``st.cache_resource`` has no guaranteed teardown on server stop, so we also
register an ``atexit`` quit as a best-effort guard against orphaned processes.
"""

from __future__ import annotations

import atexit
import threading

import chess
import chess.engine

from . import config

# Serializes access to the single interactive engine (UCI is not reentrant).
ENGINE_LOCK = threading.Lock()


def _configure(engine: chess.engine.SimpleEngine, threads: int, hash_mb: int
               ) -> chess.engine.SimpleEngine:
    """Apply Threads/Hash; ignore engines that reject an option."""
    for name, value in (("Threads", threads), ("Hash", hash_mb)):
        try:
            engine.configure({name: value})
        except Exception:
            pass
    return engine


def open_engine(path: str | None = None, *, threads: int | None = None,
                hash_mb: int | None = None) -> chess.engine.SimpleEngine:
    """Open and configure a new Stockfish process. Caller owns its lifecycle."""
    eng = chess.engine.SimpleEngine.popen_uci(path or config.resolve_engine_path())
    return _configure(eng, threads or config.ENGINE_THREADS,
                      hash_mb or config.ENGINE_HASH_MB)


def get_batch_engine(threads: int | None = None) -> chess.engine.SimpleEngine:
    """A fresh configured engine for the CLI batch job (caller must quit it).

    When running many engines in parallel, pass ``threads=1`` so the pool fills
    cores by process count rather than oversubscribing with engine threads.
    """
    return open_engine(threads=threads)


def _safe_quit(engine: chess.engine.SimpleEngine) -> None:
    try:
        engine.quit()
    except Exception:
        pass


_interactive_getter = None


def get_interactive_engine() -> chess.engine.SimpleEngine:
    """Return the process-wide interactive engine, cached across reruns.

    Wrap every ``analyse`` call on the returned engine in ``ENGINE_LOCK``.
    """
    global _interactive_getter
    if _interactive_getter is None:
        import streamlit as st

        @st.cache_resource
        def _get() -> chess.engine.SimpleEngine:
            eng = open_engine()
            atexit.register(lambda: _safe_quit(eng))
            return eng

        _interactive_getter = _get
    return _interactive_getter()


def eval_cp(board: chess.Board, depth: int = 12) -> int:
    """Evaluate `board` with the interactive engine (mover POV, centipawns)."""
    eng = get_interactive_engine()
    with ENGINE_LOCK:
        info = eng.analyse(board, chess.engine.Limit(depth=depth))
    return info["score"].pov(board.turn).score(mate_score=3000)
