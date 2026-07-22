"""Shared game-import orchestration — the one seam every source converges on.

The chess.com (``fetch``) and lichess (``lichess``) importers obtain their games
differently, but both parse into the common format — :class:`GameRecord` — and
funnel through here: each passes a ``records`` callback, and this owns the run
row, the upsert into the DB, the default-profile update, and error surfacing. So
adding a source means writing only its fetch-and-parse; the rest is source-blind.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable

from . import db
from .blitz_analysis import GameRecord

# A source's fetch-and-parse: given a progress callback, return its GameRecords.
RecordsFn = Callable[[Callable[[int], None]], Iterable[GameRecord]]


def run_import(conn: sqlite3.Connection, user: str, n: int, records: RecordsFn, *,
               source: str, default: bool = False,
               on_progress: Callable[[int], None] | None = None,
               after: Callable[[], object] | None = None) -> dict:
    """Track a fetch run, obtain GameRecords via ``records(progress)`` (the
    source-specific fetch+parse), upsert them tagged with ``source``, mark the
    default profile, and run an optional ``after`` hook (e.g. prune the raw cache).
    Fetch only — engine analysis is a separate step. Returns ``{collected,
    inserted}`` and surfaces any failure into the run row for the UI.
    """
    run_id = db.start_run(conn, user, "fetch", total=n, ts=time.time())

    def progress(count: int) -> None:
        db.update_run(conn, run_id, done=min(count, n), ts=time.time())
        if on_progress:
            on_progress(count)

    try:
        recs = list(records(progress))
        inserted = db.upsert_games(conn, recs, user, source=source)
        db.upsert_player(conn, user, default=default, ts=time.time())
        if after:
            after()
        db.update_run(conn, run_id, done=len(recs), status="done",
                      message=f"{inserted} new / {len(recs)} fetched", ts=time.time())
        return {"collected": len(recs), "inserted": inserted}
    except Exception as exc:  # surface failure into the run row for the UI
        db.update_run(conn, run_id, status="error", message=str(exc), ts=time.time())
        raise
