"""Stockfish process lifecycle.

The CLI analysis subprocess opens a fresh, configured **batch** engine per worker
and quits it itself. Nothing else in the app talks to Stockfish at runtime — the
trainer scores from cached grades — so there is no long-lived interactive engine.
"""

from __future__ import annotations

import chess.engine

from . import config


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
