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
    def test_hosted_cli_flag_forces_demo_mode(self,
                                              monkeypatch: pytest.MonkeyPatch):
        """A --hosted app argument promotes to the env var, no shell setup."""
        # Arrange: setenv registers teardown, so the promotion never leaks.
        monkeypatch.setenv("BOARDVIZ_HOSTED", "0")
        # Act
        config.promote_cli_flags(["--hosted"])
        # Assert
        assert config.hosted() is True

    @pytest.mark.spec("ENV-HOSTED")
    def test_other_args_leave_mode_alone(self,
                                         monkeypatch: pytest.MonkeyPatch):
        """Unrelated argv content never switches the mode."""
        monkeypatch.setenv("BOARDVIZ_HOSTED", "0")
        config.promote_cli_flags(["run", "--server.port", "8502"])
        assert config.hosted() is False

    @pytest.mark.spec("ENV-HOSTED")
    def test_unset_means_local(self, monkeypatch: pytest.MonkeyPatch):
        """With no env var at all, the app is a normal local install."""
        monkeypatch.delenv("BOARDVIZ_HOSTED", raising=False)
        monkeypatch.delenv("CHESSTRAIN_HOSTED", raising=False)
        assert config.hosted() is False


class TestHostedNavigation:
    """The page roster the app builds under each mode."""

    @staticmethod
    def _roster(hosted: bool) -> dict:
        """Page titles and the default page from nav_pages, read in-script.

        st.Page only populates titles within a ScriptRunContext, so a tiny
        AppTest script prints them for us.
        """
        from streamlit.testing.v1 import AppTest
        script = (
            "import json, streamlit as st\n"
            "from boardviz.ui import common\n"
            f"pages = common.nav_pages(hosted={hosted})\n"
            "st.text(json.dumps({'titles': [p.title for p in pages],\n"
            "                    'default': [p.title for p in pages\n"
            "                                if p._default]}))\n"
        )
        at = AppTest.from_string(script, default_timeout=30)
        at.run()
        assert not at.exception
        import json
        return json.loads(at.text[0].value)

    @pytest.mark.spec("ENV-HOSTED")
    def test_hosted_keeps_import_visible_but_lands_on_dashboard(self):
        """Hosted mode still lists Import (inert) and defaults to Dashboard."""
        actual = self._roster(hosted=True)
        assert "Import" in actual["titles"]
        assert actual["default"] == ["Dashboard"]

    @pytest.mark.spec("ENV-HOSTED")
    def test_local_roster_keeps_import_as_default(self):
        """Without the flag, Import stays the landing page."""
        actual = self._roster(hosted=False)
        assert actual["default"] == ["Import"]

    @pytest.mark.spec("ENV-HOSTED")
    def test_hosted_import_page_is_inert_with_notice(
            self, monkeypatch: pytest.MonkeyPatch):
        """Hosted Import shows the not-available notice; fetch is disabled."""
        # Arrange / Act: render the Import page under the flag.
        from streamlit.testing.v1 import AppTest
        monkeypatch.setenv("BOARDVIZ_HOSTED", "1")
        at = AppTest.from_string(
            "from boardviz.ui import import_page as p\np.render()\n",
            default_timeout=30)
        at.run()
        # Assert: banner present (orange warning) and fetch is disabled.
        assert not at.exception
        notices = [w.value for w in at.warning]
        assert any("Import isn't available in the hosted demo" in v
                   for v in notices)
        fetch_buttons = [b for b in at.button if "Fetch games" in b.label]
        assert len(fetch_buttons) == 1
        assert fetch_buttons[0].disabled is True

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
