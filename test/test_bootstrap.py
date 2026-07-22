"""Hosted-demo sample-database bootstrap (ENV-DEMO)."""

import io
import zipfile
from pathlib import Path

import pytest

from boardviz import bootstrap


def _sample_zip(member: str = "boardviz-sample.db",
                payload: bytes = b"not-really-sqlite") -> bytes:
    """A zip archive holding one member, as the release asset would."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, payload)
    return buf.getvalue()


class _FakeResponse:
    """Just enough of requests.Response for ensure_db."""

    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:
        """Succeeds — the fake download is always 200."""


class TestEnsureDb:
    """ensure_db downloads the sample only when needed and configured."""

    @pytest.mark.spec("ENV-DEMO")
    def test_downloads_and_installs_when_db_missing_and_url_set(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """A missing DB plus a configured URL installs the zipped sample."""
        # Arrange
        dest = tmp_path / "data" / "boardviz.db"
        payload = b"sample-db-bytes"
        monkeypatch.setattr(bootstrap.requests, "get",
                            lambda url, timeout: _FakeResponse(_sample_zip(payload=payload)))
        # Act
        installed = bootstrap.ensure_db(url="https://example.test/sample.zip",
                                        dest=dest)
        # Assert
        actual_bytes = dest.read_bytes()
        assert installed is True
        assert actual_bytes == payload

    @pytest.mark.spec("ENV-DEMO")
    def test_existing_db_is_never_touched(self, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch):
        """An existing database short-circuits before any download."""
        # Arrange: a DB already on disk, and a network that would fail loudly.
        dest = tmp_path / "boardviz.db"
        dest.write_bytes(b"existing")
        monkeypatch.setattr(bootstrap.requests, "get",
                            lambda *a, **k: pytest.fail("network touched"))
        # Act
        installed = bootstrap.ensure_db(url="https://example.test/sample.zip",
                                        dest=dest)
        # Assert
        assert installed is False
        assert dest.read_bytes() == b"existing"

    @pytest.mark.spec("ENV-DEMO")
    def test_no_url_configured_is_a_noop(self, tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch):
        """Without BOARDVIZ_SAMPLE_URL a missing DB simply stays missing."""
        # Arrange
        dest = tmp_path / "boardviz.db"
        monkeypatch.delenv(bootstrap.SAMPLE_URL_ENV, raising=False)
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
        monkeypatch.setattr(bootstrap.requests, "get",
                            lambda url, timeout: _FakeResponse(_sample_zip(member="readme.txt")))
        # Act / Assert
        with pytest.raises(ValueError, match="no .db member"):
            bootstrap.ensure_db(url="https://example.test/sample.zip", dest=dest)
