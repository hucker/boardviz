"""Sample-database bootstrap (ENV-DEMO).

A bare clone has no ``data/`` directory (it is gitignored), so a fresh
checkout — or a hosted demo such as Streamlit Community Cloud — would boot
with an empty app. When the configured database is absent or empty (no
profiles and no games; e.g. a schema created by a boot that preceded any
import), :func:`ensure_db` downloads the zipped sample database from
``config.SAMPLE_DB_URL`` (the latest release asset by default) and installs
it. Once any real data exists, this is a no-op forever.
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path
from typing import Optional

import requests

from . import config


def _has_data(path: Path) -> bool:
    """True if a database exists at ``path`` and holds any profile or game.

    A missing file, a non-database file, or a bare schema (created by a boot
    that never imported anything) all count as "no data" — safe to replace
    with the sample.
    """
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
            games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return players > 0 or games > 0


def ensure_db(url: Optional[str] = None, dest: Optional[Path] = None) -> bool:
    """Install the sample database if no real data exists yet.

    Args:
        url: Zip URL to fetch; defaults to ``config.SAMPLE_DB_URL`` (empty
            string disables the fallback).
        dest: Database path to create; defaults to ``config.DB_PATH``.

    Returns:
        True if the sample was downloaded and installed; False if data
        already exists or the fallback is disabled.

    Raises:
        requests.RequestException: If the download fails.
        ValueError: If the zip contains no ``.db`` member.
    """
    dest = Path(dest) if dest else Path(config.DB_PATH)
    if _has_data(dest):
        return False
    url = url if url is not None else config.SAMPLE_DB_URL
    if not url:
        return False

    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        members = [m for m in z.namelist() if m.endswith(".db")]
        if not members:
            raise ValueError(f"no .db member in sample zip from {url}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with z.open(members[0]) as src, open(dest, "wb") as out:
            out.write(src.read())
    return True
