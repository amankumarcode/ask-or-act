from typer.testing import CliRunner

from askoract.cli import app

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("setup", "ask", "eval", "demo", "trace", "config"):
        assert cmd in result.output


def test_config_show_resolves_profile_with_override():
    result = runner.invoke(
        app, ["config", "show", "--profile", "baseline", "--set", "agent.limits.max_steps=3"]
    )
    assert result.exit_code == 0, result.output
    assert "profile: baseline" in result.output
    assert "max_steps: 3" in result.output


def test_config_profiles_lists_shipped_profiles():
    result = runner.invoke(app, ["config", "profiles"])
    assert result.exit_code == 0
    assert "baseline" in result.output and "full" in result.output
