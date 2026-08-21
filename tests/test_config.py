"""
Runtime version reporting.

This exists so `journalctl` shows which commit and which yt-dlp were live when
something broke. Its one hard requirement is that it can never prevent the bot
from starting: a host without git, without FFmpeg on PATH, or with a hung
subprocess must still boot and simply report "unknown".
"""

import subprocess
from unittest.mock import MagicMock

import pytest

import config


# -- _first_line -------------------------------------------------------

def test_first_line_returns_the_first_line_of_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: MagicMock(
        returncode=0, stdout="1.2.3\nextra noise\n"))
    assert config._first_line(["whatever"]) == "1.2.3"


def test_first_line_returns_none_on_a_failing_command(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: MagicMock(
        returncode=128, stdout=""))
    assert config._first_line(["git", "rev-parse", "HEAD"]) is None


def test_first_line_returns_none_on_empty_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: MagicMock(
        returncode=0, stdout="   \n"))
    assert config._first_line(["whatever"]) is None


@pytest.mark.parametrize("boom", [
    FileNotFoundError("no such binary"),
    PermissionError("denied"),
    subprocess.TimeoutExpired(cmd="git", timeout=5),
])
def test_first_line_swallows_anything_the_subprocess_throws(monkeypatch, boom):
    """A missing binary or a hung command must not take the bot down."""
    def explode(*a, **k):
        raise boom
    monkeypatch.setattr(subprocess, "run", explode)
    assert config._first_line(["git"]) is None


def test_first_line_uses_a_timeout(monkeypatch):
    """Without one, a hung git call would block startup forever."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return MagicMock(returncode=0, stdout="ok\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    config._first_line(["git"])
    assert captured.get("timeout")


# -- FFmpeg version parsing --------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers",
     "6.1.1-3ubuntu5"),
    ("ffmpeg version 8.0-essentials_build-www.gyan.dev Copyright (c) 2000-2025",
     "8.0-essentials_build-www.gyan.dev"),
    ("ffmpeg version n7.0 Copyright", "n7.0"),
])
def test_ffmpeg_version_is_extracted(monkeypatch, line, expected):
    monkeypatch.setattr(config, "_first_line", lambda *a, **k: line)
    assert config._ffmpeg_version() == expected


@pytest.mark.parametrize("line", [None, "", "something else entirely", "ffmpeg"])
def test_ffmpeg_version_falls_back_to_unknown(monkeypatch, line):
    monkeypatch.setattr(config, "_first_line", lambda *a, **k: line)
    assert config._ffmpeg_version() == "unknown"


# -- runtime_versions --------------------------------------------------

def test_runtime_versions_reports_every_field():
    v = config.runtime_versions()
    assert set(v) == {"commit", "yt_dlp", "ffmpeg", "python"}
    assert all(isinstance(x, str) and x for x in v.values())


def test_python_version_is_always_real():
    """It comes from the interpreter, so it can never be unknown."""
    assert config.runtime_versions()["python"][0].isdigit()


def test_everything_degrades_to_unknown_on_a_bare_host(monkeypatch):
    """No git, no FFmpeg, no yt-dlp — the bot must still start."""
    monkeypatch.setattr(config, "_first_line", lambda *a, **k: None)
    v = config.runtime_versions()
    assert v["commit"] == "unknown"
    assert v["yt_dlp"] == "unknown"
    assert v["ffmpeg"] == "unknown"
    assert v["python"] != "unknown"


def test_commit_is_unknown_outside_a_git_checkout(monkeypatch):
    """
    The deployed host was rsynced, not cloned, so `git rev-parse` fails there.
    That must read as "unknown", not crash the boot.
    """
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: MagicMock(
        returncode=128, stdout=""))
    assert config.runtime_versions()["commit"] == "unknown"


# -- log_runtime -------------------------------------------------------

def test_log_runtime_writes_one_informative_line(caplog):
    with caplog.at_level("INFO", logger="loopify"):
        config.log_runtime()
    assert "Running commit" in caplog.text
    assert "yt-dlp" in caplog.text


def test_log_runtime_never_raises(monkeypatch):
    """Startup must not depend on version discovery succeeding."""
    monkeypatch.setattr(config, "_first_line", lambda *a, **k: None)
    config.log_runtime()


def test_log_runtime_leaks_no_secrets(caplog, monkeypatch):
    """Nothing from the environment should ever reach the log line."""
    monkeypatch.setattr(config, "DISCORD_TOKEN", "super-secret-token-value")
    with caplog.at_level("INFO", logger="loopify"):
        config.log_runtime()
    assert "super-secret-token-value" not in caplog.text


# -- .env ownership ----------------------------------------------------

def test_dotenv_is_anchored_to_the_project_root():
    """
    Searching upward from the working directory would pick up a different .env
    depending on where the bot was started from.
    """
    import os
    assert os.path.isfile(os.path.join(config.PROJECT_ROOT, "config.py"))


def test_the_systemd_unit_does_not_also_parse_env():
    """
    Two parsers over one file disagree on quoting and fail silently. Only
    python-dotenv reads .env; the unit must not declare EnvironmentFile.
    """
    import os
    setup = os.path.join(config.PROJECT_ROOT, "deploy", "setup.sh")
    with open(setup, encoding="utf-8") as handle:
        body = handle.read()
    active = [
        line for line in body.splitlines()
        if "EnvironmentFile" in line and not line.lstrip().startswith("#")
    ]
    assert active == []
