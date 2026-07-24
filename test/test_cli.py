"""CLI parsing: bare `boardviz` launches the app; subcommands still parse."""

from pathlib import Path

import pytest

from boardviz import cli


class TestDefaultCommand:
    """A no-argument invocation launches the Streamlit app (ENV-LOCAL)."""

    @pytest.mark.spec("ENV-LOCAL")
    def test_no_command_defaults_to_web(self):
        """`boardviz` with no subcommand resolves to the web launcher."""
        args = cli.build_parser().parse_args([])
        assert args.func is cli.cmd_web
        assert args.hosted is False

    @pytest.mark.spec("ENV-LOCAL")
    def test_web_subcommand_accepts_hosted(self):
        """`boardviz web --hosted` selects the launcher in demo mode."""
        args = cli.build_parser().parse_args(["web", "--hosted"])
        assert args.func is cli.cmd_web
        assert args.hosted is True

    @pytest.mark.spec("ENV-LOCAL", "ENV-HOSTED")
    def test_streamlit_argv_forwards_hosted_after_separator(self):
        """--hosted reaches the app as a script arg (after `--`), not Streamlit."""
        app = Path("x/app.py")
        plain = cli.streamlit_argv(app, hosted=False)
        demo = cli.streamlit_argv(app, hosted=True)
        assert plain == ["streamlit", "run", str(app)]
        assert demo == ["streamlit", "run", str(app), "--", "--hosted"]


class TestSubcommandsStillParse:
    """The launch default must not break the existing verbs."""

    @pytest.mark.spec("IMP-FETCH")
    def test_fetch_requires_user_and_reads_n(self):
        """`fetch --user X --n 50` binds the fetch handler with its options."""
        args = cli.build_parser().parse_args(["fetch", "--user", "X", "--n", "50"])
        assert args.func is cli.cmd_fetch
        assert args.user == "X"
        assert args.n == 50

    @pytest.mark.spec("IMP-ANLZ")
    def test_analyze_binds_its_handler(self):
        """`analyze --user X` binds the analyze handler."""
        args = cli.build_parser().parse_args(["analyze", "--user", "X"])
        assert args.func is cli.cmd_analyze
        assert args.user == "X"
