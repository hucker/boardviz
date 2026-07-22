"""Sample-database bootstrap: default-on, empty-DB self-healing (ENV-DEMO)."""

import io
import sqlite3
import zipfile
from pathlib import Path

import pytest

from boardviz import bootstrap, config


def _sample_zip(member: str = "boardviz-sample.db",
                payload: bytes = b"not-really-sqlite") -> bytes:
    """A zip archive holding one member, as the release asset would."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, payload)
    return buf.getvalue()


def _make_db(path: Path, players: int = 0) -> None:
    """A real SQLite file with the two tables _has_data probes, plus rows."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE players (username TEXT)")
    conn.execute("CREATE TABLE games (id INTEGER)")
    for i in range(players):
        conn.execute("INSERT INTO players VALUES (?)", (f"user{i}",))
    conn.commit()
    conn.close()


class _FakeResponse:
    """Just enough of requests.Response for ensure_db."""

    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:
        """Succeeds — the fake download is always 200."""


class TestEnsureDb:
    """ensure_db installs the sample only when no real data exists."""

    @pytest.mark.spec("ENV-DEMO")
    def test_downloads_and_installs_when_db_missing(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """A missing DB file installs the zipped sample."""
        # Arrange
        dest = tmp_path / "data" / "boardviz.db"
        payload = b"sample-db-bytes"
        monkeypatch.setattr(
            bootstrap.requests, "get",
            lambda url, timeout: _FakeResponse(_sample_zip(payload=payload)))
        # Act
        installed = bootstrap.ensure_db(url="https://example.test/sample.zip",
                                        dest=dest)
        # Assert
        actual_bytes = dest.read_bytes()
        assert installed is True
        assert actual_bytes == payload

    @pytest.mark.spec("ENV-DEMO")
    def test_empty_schema_db_is_replaced(self, tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch):
        """A schema with no profiles/games (pre-import boot) is self-healed.

        This is the hosted-demo failure mode: a first boot without the sample
        creates an empty database, which must not block a later bootstrap.
        """
        # Arrange: an initialized but dataless DB, as a secretless boot leaves.
        dest = tmp_path / "boardviz.db"
        _make_db(dest, players=0)
        payload = b"sample-db-bytes"
        monkeypatch.setattr(
            bootstrap.requests, "get",
            lambda url, timeout: _FakeResponse(_sample_zip(payload=payload)))
        # Act
        installed = bootstrap.ensure_db(url="https://example.test/sample.zip",
                                        dest=dest)
        # Assert
        assert installed is True
        assert dest.read_bytes() == payload

    @pytest.mark.spec("ENV-DEMO")
    def test_db_with_data_is_never_touched(self, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch):
        """Any real data (a profile) short-circuits before any download."""
        # Arrange: a DB with a profile, and a network that would fail loudly.
        dest = tmp_path / "boardviz.db"
        _make_db(dest, players=1)
        monkeypatch.setattr(bootstrap.requests, "get",
                            lambda *a, **k: pytest.fail("network touched"))
        # Act
        installed = bootstrap.ensure_db(url="https://example.test/sample.zip",
                                        dest=dest)
        # Assert
        assert installed is False

    @pytest.mark.spec("ENV-DEMO")
    def test_default_url_comes_from_config(self, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch):
        """With no explicit url, ensure_db fetches config.SAMPLE_DB_URL."""
        # Arrange
        dest = tmp_path / "boardviz.db"
        seen = {}

        def fake_get(url, timeout):
            seen["url"] = url
            return _FakeResponse(_sample_zip())

        monkeypatch.setattr(config, "SAMPLE_DB_URL", "https://cfg.test/s.zip")
        monkeypatch.setattr(bootstrap.requests, "get", fake_get)
        # Act
        installed = bootstrap.ensure_db(dest=dest)
        # Assert
        assert installed is True
        assert seen["url"] == "https://cfg.test/s.zip"

    @pytest.mark.spec("ENV-DEMO")
    def test_empty_url_disables_fallback(self, tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch):
        """BOARDVIZ_SAMPLE_URL="" (empty SAMPLE_DB_URL) turns the feature off."""
        # Arrange
        dest = tmp_path / "boardviz.db"
        monkeypatch.setattr(config, "SAMPLE_DB_URL", "")
        monkeypatch.setattr(bootstrap.requests, "get",
                            lambda *a, **k: pytest.fail("network touched"))
        # Act
        installed = bootstrap.ensure_db(dest=dest)
        # Assert
        assert installed is False
        assert not dest.exists()

    @pytest.mark.spec("ENV-DEMO")
    def test_zip_without_db_member_raises(self, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch):
        """A zip with no .db member is a configuration error, not silence."""
        # Arrange
        dest = tmp_path / "boardviz.db"
        monkeypatch.setattr(
            bootstrap.requests, "get",
            lambda url, timeout: _FakeResponse(_sample_zip(member="readme.txt")))
        # Act / Assert
        with pytest.raises(ValueError, match="no .db member"):
            bootstrap.ensure_db(url="https://example.test/sample.zip", dest=dest)
