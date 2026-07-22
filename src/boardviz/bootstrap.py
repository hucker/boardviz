"""Sample-database bootstrap for hosted demos (ENV-DEMO).

A bare clone has no ``data/`` directory (it is gitignored), so a hosted demo
such as Streamlit Community Cloud would boot with an empty app. When the
configured database file is absent and ``BOARDVIZ_SAMPLE_URL`` points at a
zipped sample database (a GitHub release asset), :func:`ensure_db` downloads
and unpacks it once at startup. Locally, where a database already exists or
no URL is set, this is a no-op.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import Optional

import requests

from . import config

SAMPLE_URL_ENV = "BOARDVIZ_SAMPLE_URL"


def ensure_db(url: Optional[str] = None, dest: Optional[Path] = None) -> bool:
    """Download and unpack the sample database if none exists yet.

    Args:
        url: Zip URL to fetch; defaults to ``$BOARDVIZ_SAMPLE_URL``.
        dest: Database path to create; defaults to ``config.DB_PATH``.

    Returns:
        True if a sample database was downloaded and installed; False if the
        database already existed or no URL is configured.

    Raises:
        requests.HTTPError: If the download fails.
        ValueError: If the zip contains no ``.db`` member.
    """
    dest = Path(dest) if dest else Path(config.DB_PATH)
    if dest.exists():
        return False
    url = url or os.environ.get(SAMPLE_URL_ENV)
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
