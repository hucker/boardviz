"""Lichess import client.

Lichess exposes a games-export API that *streams PGN* for a user's games,
newest-first. We fetch up to N, cache the raw PGN to ``data/archives/{user}/``,
and parse the PGN headers straight into :class:`GameRecord`\\ s — the same shape
the chess.com importer produces — so the rest of the pipeline (upsert, analysis,
trainer) is source-agnostic. The game's ``Site`` header
(``https://lichess.org/<id>``) is stored as the URL, which is how the app tells a
lichess game from a chess.com one (see ``common.game_source``).

A descriptive ``User-Agent`` is polite; an optional API token
(``BOARDVIZ_LICHESS_TOKEN`` / ``LICHESS_TOKEN``) raises the rate limit, but public
games import without one.
"""

from __future__ import annotations

import datetime as dt
import io
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

import chess
import chess.pgn
import requests

from . import config, ingest
from .blitz_analysis import GameRecord

SOURCE = "lichess"
EXPORT_URL = "https://lichess.org/api/games/user/{user}"
# tc_class -> lichess "perfType" for the optional speed filter.
_PERF = {"bullet": "bullet", "blitz": "blitz", "rapid": "rapid",
         "daily": "correspondence"}


def _token() -> str | None:
    return os.environ.get("BOARDVIZ_LICHESS_TOKEN") or os.environ.get("LICHESS_TOKEN")


def _pgn_cache(user: str) -> Path:
    d = config.ARCHIVES_DIR / user.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d / "lichess.pgn"


def fetch_pgn(user: str, n: int, *, tc_class: str | None = None,
              session: requests.Session | None = None) -> str:
    """Fetch up to ``n`` of ``user``'s games from lichess as PGN (newest first),
    caching the raw text. ``clocks``/``opening`` are requested so the analysis has
    move times and the DB has opening names. Raises on an HTTP error (e.g. 429)."""
    s = session or requests.Session()
    headers = {"User-Agent": config.HTTP_USER_AGENT,
               "Accept": "application/x-chess-pgn"}
    if tok := _token():
        headers["Authorization"] = f"Bearer {tok}"
    params: dict = {"max": n, "clocks": "true", "opening": "true",
                    "sort": "dateDesc"}
    if tc_class and tc_class in _PERF:
        params["perfType"] = _PERF[tc_class]
    resp = s.get(EXPORT_URL.format(user=user.lower()), headers=headers,
                 params=params, timeout=60)
    resp.raise_for_status()
    _pgn_cache(user).write_text(resp.text, encoding="utf-8")
    return resp.text


def _end_time(h: chess.pgn.Headers) -> dt.datetime:
    """Game time from the UTCDate/UTCTime headers (epoch 0 if unparseable)."""
    date, tm = h.get("UTCDate") or h.get("Date") or "", h.get("UTCTime") or "00:00:00"
    try:
        return dt.datetime.strptime(f"{date} {tm}", "%Y.%m.%d %H:%M:%S")
    except ValueError:
        return dt.datetime.fromtimestamp(0)


def _game_id(site: str) -> str:
    """The 8-char game id from a ``https://lichess.org/<id>`` Site header."""
    return site.rstrip("/").rsplit("/", 1)[-1][:8]


def _termination(game: chess.pgn.Game, term: str, decisive: bool) -> str:
    """Normalise a lichess Termination to the chess.com-flavoured wording
    :func:`db.classify_end_method` understands. Lichess only says "Normal" /
    "Time forfeit" / "Abandoned", so infer checkmate from the final position."""
    if term == "Time forfeit":
        return "won on time"
    if term == "Normal" and decisive:
        return "checkmate" if game.end().board().is_checkmate() else "resignation"
    return term


def records_from_pgn(pgn_text: str, username: str) -> list[GameRecord]:
    """Parse a lichess PGN stream into GameRecords in the tracked player's POV."""
    out: list[GameRecord] = []
    stream = io.StringIO(pgn_text)
    uname = username.lower()
    while (game := chess.pgn.read_game(stream)) is not None:
        h = game.headers
        if h.get("Variant", "Standard") != "Standard":  # standard chess only
            continue
        my_color = chess.WHITE if h.get("White", "").lower() == uname else chess.BLACK
        res = h.get("Result", "*")
        won = ((res == "1-0" and my_color == chess.WHITE)
               or (res == "0-1" and my_color == chess.BLACK))
        outcome = "win" if won else ("draw" if res == "1/2-1/2" else "loss")
        term = h.get("Termination", "")
        site = h.get("Site", "")
        out.append(GameRecord(
            game=game,
            url=site,
            my_color=my_color,
            outcome=outcome,
            termination=_termination(game, term, res in ("1-0", "0-1")),
            time_control=h.get("TimeControl", ""),
            end_time=_end_time(h),
            flagged=(outcome == "loss" and term == "Time forfeit"),
            uuid=_game_id(site),
            pgn=str(game),  # re-serialised: keeps headers, moves and %clk comments
        ))
    return out


def import_user_games(conn: sqlite3.Connection, user: str, n: int, *,
                      default: bool = False, tc_class: str | None = None,
                      on_progress: Callable[[int], None] | None = None) -> dict:
    """Fetch the last ``n`` lichess games for ``user`` and upsert them. Same
    signature and shared orchestration (:func:`ingest.run_import`) as the chess.com
    importer — a single PGN request, no per-game progress; returns
    ``{collected, inserted}``."""
    def records(_progress: Callable[[int], None]) -> list[GameRecord]:
        return records_from_pgn(fetch_pgn(user, n, tc_class=tc_class), user)

    return ingest.run_import(conn, user, n, records, source=SOURCE, default=default,
                             on_progress=on_progress)
