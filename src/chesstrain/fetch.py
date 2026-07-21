"""chess.com import client.

The public API only exposes monthly archives, so "last N games" means: list the
archive months, walk them newest-first, and accumulate games until N are in
hand. Every raw month is cached to ``data/archives/{user}/`` so re-imports and
the analysis subprocess never re-hit the network.

A descriptive ``User-Agent`` is mandatory — chess.com returns 403 without one.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import requests

from . import config, db
from .blitz_analysis import GameRecord, load_games

ARCHIVES_URL = "https://api.chess.com/pub/player/{user}/games/archives"


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.HTTP_USER_AGENT})
    return s


def list_archives(user: str, session: requests.Session | None = None) -> list[str]:
    """Return monthly-archive URLs for a user, oldest-first (chess.com order)."""
    session = session or make_session()
    resp = session.get(ARCHIVES_URL.format(user=user.lower()), timeout=30)
    resp.raise_for_status()
    return resp.json().get("archives", [])


def _archive_path(user: str, year: int, month: int) -> Path:
    d = config.ARCHIVES_DIR / user.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{year:04d}-{month:02d}.json"


def fetch_month(user: str, year: int, month: int,
                session: requests.Session | None = None,
                cache: bool = True) -> dict | None:
    """Fetch one month's games, caching raw JSON to disk. None if 404."""
    session = session or make_session()
    path = _archive_path(user, year, month)
    url = f"https://api.chess.com/pub/player/{user.lower()}/games/{year:04d}/{month:02d}"
    resp = session.get(url, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    if cache:
        path.write_text(json.dumps(data))
    return data


def _archive_year_month(url: str) -> tuple[int, int]:
    parts = url.rstrip("/").split("/")
    return int(parts[-2]), int(parts[-1])


def fetch_until_n(user: str, n: int, tc_class: str | None = None,
                  session: requests.Session | None = None,
                  on_progress: Callable[[int], None] | None = None) -> list[dict]:
    """Walk archives newest-first, returning up to `n` raw game dicts.

    Args:
        user: chess.com username.
        n: target number of games.
        tc_class: keep only this time-control class (bullet/blitz/rapid/daily).
        on_progress: called with the running collected count after each month.

    Returns:
        Raw game dicts (newest-first), length <= n.
    """
    session = session or make_session()
    archives = list_archives(user, session)
    collected: list[dict] = []
    for url in reversed(archives):  # newest month first
        year, month = _archive_year_month(url)
        data = fetch_month(user, year, month, session)
        if data is None:
            continue
        games = data.get("games", [])
        games.sort(key=lambda g: g.get("end_time", 0), reverse=True)
        if tc_class is not None:
            games = [g for g in games
                     if config.tc_class(g.get("time_control", "")) == tc_class]
        collected.extend(games)
        if on_progress:
            on_progress(len(collected))
        if len(collected) >= n:
            break
    return collected[:n]


def months_between(start: dt.date, end: dt.date) -> Iterator[tuple[int, int]]:
    """Yield (year, month) covering [start, end] inclusive."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fetch_date_range(user: str, start: dt.date, end: dt.date,
                     tc_class: str | None = None,
                     session: requests.Session | None = None) -> list[dict]:
    """Fetch all games with end_time within [start, end] (UTC day bounds)."""
    session = session or make_session()
    lo = dt.datetime.combine(start, dt.time.min).timestamp()
    hi = dt.datetime.combine(end, dt.time.max).timestamp()
    out: list[dict] = []
    for year, month in months_between(start, end):
        data = fetch_month(user, year, month, session)
        if data is None:
            continue
        for g in data.get("games", []):
            if lo <= g.get("end_time", 0) <= hi and (
                tc_class is None
                or config.tc_class(g.get("time_control", "")) == tc_class
            ):
                out.append(g)
    out.sort(key=lambda g: g.get("end_time", 0), reverse=True)
    return out


def _records_from_raw(user: str, raw: list[dict]) -> list[GameRecord]:
    """Parse+classify raw game dicts via load_games (writes a merged file)."""
    merged = config.ARCHIVES_DIR / user.lower() / "_merged.json"
    merged.parent.mkdir(parents=True, exist_ok=True)
    merged.write_text(json.dumps({"games": raw}))
    return load_games(merged, username=user, time_control=None)


def _month_archives(user: str) -> list[Path]:
    """A profile's cached ``YYYY-MM.json`` month files, oldest→newest (by name)."""
    d = config.ARCHIVES_DIR / user.lower()
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.json") if p.name != "_merged.json")


def prune_archives(user: str, keep: int = config.ARCHIVE_KEEP) -> list[str]:
    """Keep only the newest ``keep`` cached month files for ``user`` (bounds disk).

    Returns the names of the files deleted. ``_merged.json`` is never touched.
    Deleting older months bounds what a rebuild can recover to the kept months.
    """
    files = _month_archives(user)
    stale = files[:-keep] if keep > 0 else files
    for p in stale:
        p.unlink()
    return [p.name for p in stale]


def records_from_archives(user: str) -> list[GameRecord]:
    """Every game across a profile's cached month files — the rebuild source."""
    out: list[GameRecord] = []
    for path in _month_archives(user):
        out.extend(load_games(path, username=user, time_control=None))
    return out


def import_user_games(conn: sqlite3.Connection, user: str, n: int, *,
                      default: bool = False, tc_class: str | None = None,
                      on_progress: Callable[[int], None] | None = None) -> dict:
    """Fetch the last `n` games for `user` and upsert them into the DB.

    ``default`` makes this the default profile (see db.upsert_player). Returns a
    summary dict: {collected, inserted}. Only fetch happens here (fast); engine
    analysis is a separate step. The raw month cache is pruned afterwards.
    """
    ts = time.time()
    run_id = db.start_run(conn, user, "fetch", total=n, ts=ts)

    def progress(count: int) -> None:
        db.update_run(conn, run_id, done=min(count, n), ts=time.time())
        if on_progress:
            on_progress(count)

    try:
        raw = fetch_until_n(user, n, tc_class=tc_class, on_progress=progress)
        records = _records_from_raw(user, raw)
        inserted = db.upsert_games(conn, records, user)
        db.upsert_player(conn, user, default=default, ts=time.time())
        prune_archives(user)
        db.update_run(conn, run_id, done=len(raw), status="done",
                      message=f"{inserted} new / {len(raw)} fetched",
                      ts=time.time())
        return {"collected": len(raw), "inserted": inserted}
    except Exception as exc:  # surface failure into the run row for the UI
        db.update_run(conn, run_id, status="error", message=str(exc),
                      ts=time.time())
        raise
