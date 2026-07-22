"""Read-only hosted-demo mode: the BOARDVIZ_HOSTED switch (ENV-HOSTED)."""

import pytest

from boardviz import config


class TestHostedFlag:
    """config.hosted() reads the env switch at call time."""

    @pytest.mark.spec("ENV-HOSTED")
    @pytest.mark.parametrize("value, expected", [
        ("1", True),
        ("true", True),
        ("0", False),
        ("", False),
    ])
    def test_hosted_parses_the_env_value(self, monkeypatch: pytest.MonkeyPatch,
                                         value: str, expected: bool):
        """Non-empty, non-'0' values switch hosted mode on."""
        monkeypatch.setenv("BOARDVIZ_HOSTED", value)
        actual_hosted = config.hosted()
        assert actual_hosted == expected

    @pytest.mark.spec("ENV-HOSTED")
    def test_unset_means_local(self, monkeypatch: pytest.MonkeyPatch):
        """With no env var at all, the app is a normal local install."""
        monkeypatch.delenv("BOARDVIZ_HOSTED", raising=False)
        monkeypatch.delenv("CHESSTRAIN_HOSTED", raising=False)
        assert config.hosted() is False


class TestHostedNavigation:
    """The page roster the app builds under each mode."""

    @staticmethod
    def _roster_titles(hosted: bool) -> list[str]:
        """Page titles from nav_pages, read inside a real script run.

        st.Page only populates titles within a ScriptRunContext, so a tiny
        AppTest script prints them for us.
        """
        from streamlit.testing.v1 import AppTest
        script = (
            "import json, streamlit as st\n"
            "from boardviz.ui import common\n"
            f"titles = [p.title for p in common.nav_pages(hosted={hosted})]\n"
            "st.text(json.dumps(titles))\n"
        )
        at = AppTest.from_string(script, default_timeout=30)
        at.run()
        assert not at.exception
        import json
        return json.loads(at.text[0].value)

    @pytest.mark.spec("ENV-HOSTED")
    def test_hosted_roster_has_no_import_page(self):
        """Hosted mode drops Import entirely; Dashboard leads the roster."""
        actual_titles = self._roster_titles(hosted=True)
        expected_titles = ["Dashboard", "Review", "Mate review", "Trainer"]
        assert actual_titles == expected_titles

    @pytest.mark.spec("ENV-HOSTED")
    def test_local_roster_keeps_import_first(self):
        """Without the flag, Import stays and leads the roster."""
        actual_titles = self._roster_titles(hosted=False)
        assert actual_titles[0] == "Import"

    @pytest.mark.spec("ENV-HOSTED")
    def test_hosted_app_lands_on_dashboard(self,
                                           monkeypatch: pytest.MonkeyPatch):
        """The real entrypoint under the flag renders the Dashboard page."""
        # Arrange / Act: run the actual app script in hosted mode.
        from streamlit.testing.v1 import AppTest
        monkeypatch.setenv("BOARDVIZ_HOSTED", "1")
        at = AppTest.from_file("src/boardviz/app.py", default_timeout=30)
        at.run()
        # Assert: no exception, and the Dashboard header rendered.
        headers = [h.value for h in at.header]
        assert not at.exception
        assert "📊 Dashboard" in headers
        assert "📥 Import games" not in headers
