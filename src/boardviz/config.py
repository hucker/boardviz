"""Central configuration: paths, engine resolution, time-control classes,
time-penalty curves, and analysis thresholds.

Everything the rest of the app needs to locate files, find Stockfish, classify
a chess.com ``time_control`` string, and score the trainer lives here so there
is a single place to tune behavior. Grading (eval-loss) thresholds stay in
``blitz_analysis`` since they belong to the domain engine; this module owns the
*time* dimension and the file layout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# --- File layout -----------------------------------------------------------
# PACKAGE_DIR = .../src/boardviz ; PROJECT_ROOT = repo root (two up).
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent.parent


def _env(new: str, old: str | None = None) -> str | None:
    """An env var by its current ``BOARDVIZ_*`` name, falling back to the
    pre-rename ``CHESSTRAIN_*`` name so an old setup keeps working."""
    v = os.environ.get(new)
    return v if v is not None else (os.environ.get(old) if old else None)


def _dir(new_env: str, old_env: str, default: Path) -> Path:
    """Resolve a directory from an env override (new or legacy name), creating it."""
    override = _env(new_env, old_env)
    p = Path(override).expanduser() if override else default
    p.mkdir(parents=True, exist_ok=True)
    return p


DATA_DIR = _dir("BOARDVIZ_DATA_DIR", "CHESSTRAIN_DATA_DIR", PROJECT_ROOT / "data")
ARCHIVES_DIR = _dir(
    "BOARDVIZ_ARCHIVES_DIR", "CHESSTRAIN_ARCHIVES_DIR", DATA_DIR / "archives")
# How many raw month files to keep per profile (a fetch prunes older ones). These
# are the DB rebuild source, so this also bounds what a rebuild can recover.
ARCHIVE_KEEP = int(_env("BOARDVIZ_ARCHIVE_KEEP", "CHESSTRAIN_ARCHIVE_KEEP") or "10")
ENGINES_DIR = PROJECT_ROOT / "engines"
# Default DB is data/boardviz.db, but if a pre-rename data/chesstrain.db exists we
# keep using it — renaming the package must never orphan an existing database.
_legacy_db = DATA_DIR / "chesstrain.db"
_default_db = _legacy_db if _legacy_db.exists() else DATA_DIR / "boardviz.db"
DB_PATH = Path(_env("BOARDVIZ_DB", "CHESSTRAIN_DB") or _default_db)

def hosted() -> bool:
    """True when running as a read-only hosted demo (ENV-HOSTED).

    Set ``BOARDVIZ_HOSTED`` to any value other than empty or ``0`` (on
    Streamlit Community Cloud: a line in the app's Secrets). Makes the
    Import page inert, so no fetching or analysis can run in the cloud.
    """
    return (_env("BOARDVIZ_HOSTED") or "0") not in ("", "0")


def promote_cli_flags(argv: Optional[list[str]] = None) -> None:
    """Promote app arguments to their env-var equivalents (ENV-HOSTED).

    ``--hosted`` forces demo mode without setting an env var — the local way
    to preview it: ``streamlit run src/boardviz/app.py -- --hosted``.

    Args:
        argv: Argument list to scan; defaults to ``sys.argv[1:]``.
    """
    args = sys.argv[1:] if argv is None else argv
    if "--hosted" in args:
        os.environ["BOARDVIZ_HOSTED"] = "1"


# Sample database the app boots from when it has no data (ENV-DEMO): the
# latest release asset by default. BOARDVIZ_SAMPLE_URL points elsewhere;
# setting it to an empty string disables the fallback.
_sample_override = _env("BOARDVIZ_SAMPLE_URL")
SAMPLE_DB_URL = _sample_override if _sample_override is not None else (
    "https://github.com/hucker/boardviz/releases/latest/download/boardviz-sample.zip"
)

# chess.com requires a descriptive User-Agent or it returns 403.
HTTP_USER_AGENT = _env("BOARDVIZ_USER_AGENT", "CHESSTRAIN_USER_AGENT") or (
    "boardviz/0.1 (personal analysis; chuck@acrocad.net)"
)


class EngineNotFound(RuntimeError):
    """Raised when no Stockfish binary can be located."""


def resolve_engine_path() -> str:
    """Locate the Stockfish binary.

    Order: ``$STOCKFISH_PATH`` -> a binary vendored in ``engines/`` -> error.
    The vendored binary is gitignored and never shipped in the wheel (GPL); it
    is placed here by the user on first setup (see README).

    Raises:
        EngineNotFound: with setup instructions if nothing is found.
    """
    env = os.environ.get("STOCKFISH_PATH")
    if env and Path(env).is_file():
        return env
    for name in ("stockfish.exe", "stockfish"):
        cand = ENGINES_DIR / name
        if cand.is_file():
            return str(cand)
    raise EngineNotFound(
        "No Stockfish binary found. Set the STOCKFISH_PATH environment variable, "
        f"or place stockfish.exe in {ENGINES_DIR}. Download from "
        "https://stockfishchess.org/download/ (Windows AVX2 build)."
    )


# --- Time-control classification ------------------------------------------
def base_seconds(time_control: str) -> int | None:
    """Parse a chess.com ``time_control`` to base seconds.

    Returns None for anything without a numeric base: daily ("1/86400"),
    untimed ("-"), or empty.
    """
    if not time_control or "/" in time_control:  # "1/86400" = daily correspondence
        return None
    base = time_control.split("+", 1)[0]
    return int(base) if base.isdigit() else None


def tc_class(time_control: str) -> str:
    """Classify a ``time_control`` as bullet/blitz/rapid/daily.

    Boundaries follow chess.com: bullet <3min, blitz 3-<10min, rapid >=10min.
    """
    base = base_seconds(time_control)
    if base is None:
        return "daily"
    if base < 180:
        return "bullet"
    if base < 600:
        return "blitz"
    return "rapid"


# Big-think thresholds (seconds) per class: a "long think" for the analytics.
LONG_THINK_S: dict[str, float] = {
    "bullet": 6.0,
    "blitz": 15.0,
    "rapid": 30.0,
    "daily": 120.0,
}

# Time-penalty curves per class: (elapsed_seconds_threshold, penalty), most
# severe applicable one wins. Blitz: -1 after 10s, -2 after 20s (req 5).
TIME_PENALTY_CURVES: dict[str, list[tuple[float, int]]] = {
    "bullet": [(4.0, -1), (8.0, -2)],
    "blitz": [(10.0, -1), (20.0, -2)],
    "rapid": [(30.0, -1), (60.0, -2)],
    "daily": [],
}

# "Winning/losing by more than X" readout threshold (centipawns, mover POV).
WIN_THRESHOLD_CP = 200

# Engine resources for the batch pass.
ENGINE_THREADS = int(_env("BOARDVIZ_ENGINE_THREADS", "CHESSTRAIN_ENGINE_THREADS") or "2")
ENGINE_HASH_MB = int(_env("BOARDVIZ_ENGINE_HASH", "CHESSTRAIN_ENGINE_HASH") or "256")
