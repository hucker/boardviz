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
from pathlib import Path

# --- File layout -----------------------------------------------------------
# PACKAGE_DIR = .../src/chesstrain ; PROJECT_ROOT = repo root (two up).
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent.parent


def _dir(env: str, default: Path) -> Path:
    """Resolve a directory from an env override, creating it if needed."""
    p = Path(os.environ[env]).expanduser() if os.environ.get(env) else default
    p.mkdir(parents=True, exist_ok=True)
    return p


DATA_DIR = _dir("CHESSTRAIN_DATA_DIR", PROJECT_ROOT / "data")
ARCHIVES_DIR = _dir("CHESSTRAIN_ARCHIVES_DIR", DATA_DIR / "archives")
ENGINES_DIR = PROJECT_ROOT / "engines"
DB_PATH = Path(os.environ.get("CHESSTRAIN_DB", DATA_DIR / "chesstrain.db"))

# chess.com requires a descriptive User-Agent or it returns 403.
HTTP_USER_AGENT = os.environ.get(
    "CHESSTRAIN_USER_AGENT", "chesstrain/0.1 (personal analysis; chuck@acrocad.net)"
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
ENGINE_THREADS = int(os.environ.get("CHESSTRAIN_ENGINE_THREADS", "2"))
ENGINE_HASH_MB = int(os.environ.get("CHESSTRAIN_ENGINE_HASH", "256"))
