"""Cut a boardviz release: preflight checks, sample DB, build, tag, GitHub Release.

Run from the repo root on an up-to-date ``main``:

    uv run python scripts/release.py            # full release
    uv run python scripts/release.py --dry-run  # everything except tag/push/release

What it does, in order:

1. Preflight — must be on ``main`` with a clean tree; ``pytest`` and
   ``ruff check`` must pass; the tag ``v<version>`` (from pyproject.toml)
   must not already exist.
2. Sample DB — prunes the live database down to the ``SAMPLE_PROFILE``
   profile's most recent ``SAMPLE_GAMES`` analyzed games (drill history and
   import logs cleared), vacuums it, and zips it to ``dist/boardviz-sample.zip``.
3. Build — ``uv build`` produces the wheel and sdist in ``dist/``.
4. Release — tags ``v<version>``, pushes the tag, and creates a GitHub
   Release with the sample zip, wheel, and sdist attached. Publishing the
   release triggers ``.github/workflows/release.yml``, which uploads the
   wheel/sdist to PyPI via trusted publishing.

To release a new version: bump ``version`` in pyproject.toml first (the tag
is derived from it), merge to ``main``, then run this script.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"

# Sample database shipped as a release asset so people can try the app
# without importing/analyzing their own games first.
SAMPLE_PROFILE = "hikaru"
SAMPLE_GAMES = 500
SAMPLE_DB_NAME = "boardviz-sample.db"
SAMPLE_ZIP_NAME = "boardviz-sample.zip"


def run(*cmd: str, capture: bool = False) -> str:
    """Run a command from the repo root, failing the release if it fails.

    Args:
        *cmd: The command and its arguments.
        capture: If True, return stdout (stripped) instead of echoing it.

    Returns:
        Captured stdout when ``capture`` is True, else "".
    """
    print(f"  $ {' '.join(cmd)}", flush=True)
    result = subprocess.run(
        cmd, cwd=REPO_ROOT, text=True, capture_output=capture, check=False
    )
    if result.returncode != 0:
        if capture:
            sys.stderr.write(result.stderr or result.stdout or "")
        sys.exit(f"release aborted: `{' '.join(cmd)}` failed")
    return (result.stdout or "").strip() if capture else ""


def project_version() -> str:
    """The version declared in pyproject.toml."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def preflight(version: str, dry_run: bool) -> None:
    """Refuse to release from a dirty tree, wrong branch, or failing checks.

    Args:
        version: The version about to be released (checked against existing tags).
        dry_run: When True, skip the branch/clean-tree checks so a dry run
            can be exercised from a work branch; checks and tag guard still run.
    """
    if not dry_run:
        branch = run("git", "rev-parse", "--abbrev-ref", "HEAD", capture=True)
        if branch != "main":
            sys.exit(f"release aborted: on branch {branch!r}, releases cut from main")
        if run("git", "status", "--porcelain", capture=True):
            sys.exit("release aborted: working tree not clean")
    tags = run("git", "tag", "--list", f"v{version}", capture=True)
    if tags:
        sys.exit(
            f"release aborted: tag v{version} already exists; "
            "bump version in pyproject.toml"
        )
    run("uv", "run", "ruff", "check")
    # -m form: a bare `uv run pytest` trips over the .venv script shim on
    # this Windows setup ("Failed to canonicalize script path").
    run("uv", "run", "-m", "pytest", "-q")


def build_sample_db(live_db: Path) -> Path:
    """Prune a copy of the live DB to the sample profile and zip it.

    Keeps the ``SAMPLE_GAMES`` most recent analyzed games for
    ``SAMPLE_PROFILE`` (with their moves, mistakes, grades, and mate
    chances), clears the drill/import history, vacuums, and zips.

    Args:
        live_db: Path to the full local database to sample from.

    Returns:
        Path to the created zip in ``dist/``.
    """
    if not live_db.exists():
        sys.exit(f"release aborted: live database not found at {live_db}")
    DIST_DIR.mkdir(exist_ok=True)
    sample = DIST_DIR / SAMPLE_DB_NAME
    shutil.copyfile(live_db, sample)
    conn = sqlite3.connect(sample)
    conn.executescript(
        f"""
        CREATE TEMP TABLE keep AS
          SELECT id FROM games
          WHERE username='{SAMPLE_PROFILE}' AND analyzed=1
          ORDER BY end_time DESC LIMIT {SAMPLE_GAMES};
        DELETE FROM games WHERE id NOT IN (SELECT id FROM keep);
        DELETE FROM moves WHERE game_id NOT IN (SELECT id FROM keep);
        DELETE FROM mistakes WHERE game_id NOT IN (SELECT id FROM keep);
        DELETE FROM mate_chances WHERE game_id NOT IN (SELECT id FROM keep);
        DELETE FROM grades_cache WHERE epd NOT IN (SELECT epd FROM mistakes);
        DELETE FROM attempts;
        DELETE FROM import_runs;
        DELETE FROM players WHERE username <> '{SAMPLE_PROFILE}';
        UPDATE players SET is_default=1 WHERE username='{SAMPLE_PROFILE}';
        """
    )
    conn.commit()
    kept = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    conn.execute("VACUUM")
    conn.close()
    if kept < SAMPLE_GAMES:
        print(f"  note: only {kept} analyzed {SAMPLE_PROFILE} games available")
    zip_path = DIST_DIR / SAMPLE_ZIP_NAME
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(sample, SAMPLE_DB_NAME)
    sample.unlink()  # only the zip is shipped
    print(f"  sample: {kept} games, {zip_path.stat().st_size / 1e6:.1f} MB zipped")
    return zip_path


def main() -> None:
    """Drive the release, stopping before anything irreversible on --dry-run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="run preflight, sample DB, and build; skip tag/push/release",
    )
    args = parser.parse_args()

    # Imported late so a broken checkout fails in preflight, not at import.
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from boardviz import config

    version = project_version()
    print(f"Releasing boardviz {version}")

    print("Preflight:")
    preflight(version, args.dry_run)

    print("Sample database:")
    sample_zip = build_sample_db(Path(config.DB_PATH))

    print("Build:")
    wheel = DIST_DIR / f"boardviz-{version}-py3-none-any.whl"
    sdist = DIST_DIR / f"boardviz-{version}.tar.gz"
    wheel.unlink(missing_ok=True)
    sdist.unlink(missing_ok=True)
    run("uv", "build")
    for artifact in (wheel, sdist):
        if not artifact.exists():
            sys.exit(f"release aborted: expected build artifact missing: {artifact}")

    if args.dry_run:
        print(f"Dry run complete: v{version} ready; nothing tagged or published.")
        return

    reply = input(f"Tag v{version}, push, and publish the GitHub Release? [y/N] ")
    if reply.strip().lower() != "y":
        sys.exit("release aborted: not confirmed")

    print("Tag and release:")
    run("git", "tag", f"v{version}")
    run("git", "push", "origin", f"v{version}")
    run(
        "gh", "release", "create", f"v{version}",
        str(wheel), str(sdist), str(sample_zip),
        "--title", f"boardviz {version}",
        "--generate-notes",
    )
    print(
        f"Done: v{version} released. PyPI publish runs in GitHub Actions "
        "(watch: gh run watch)."
    )


if __name__ == "__main__":
    main()
