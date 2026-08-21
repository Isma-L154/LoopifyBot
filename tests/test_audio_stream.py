"""
``services.media.AudioStream`` - the yt-dlp process that feeds FFmpeg.

These tests spawn **real** subprocesses (a short Python one-liner, never
yt-dlp) because the bugs being guarded against are about operating-system pipe
behaviour and process reaping, which mocks cannot reproduce.
"""

import subprocess
import sys

import pytest

from services import media
from services.media import AudioStream


def script(body: str) -> list:
    return [sys.executable, "-c", body]


@pytest.fixture
def spawned():
    """Tracks streams so a failing test cannot leave a process behind."""
    created = []

    def _launch(body: str) -> AudioStream:
        stream = AudioStream.launch(script(body))
        created.append(stream)
        return stream

    yield _launch
    for stream in created:
        stream.close()


# -- the deadlock this class exists to prevent -------------------------

def test_stderr_is_never_a_pipe(monkeypatch):
    """
    Regression guard for the stall bug.

    Nothing reads stderr while a track plays, so if stderr were a pipe, a
    chatty yt-dlp would fill the ~64 KB kernel buffer, block on write and stop
    producing audio. It must go somewhere that never blocks.
    """
    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured.update(kwargs)
            self.stdout = None
            self.stderr = None

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    AudioStream.launch(["true"])

    assert captured["stderr"] is not subprocess.PIPE
    assert captured["stderr"] is not None, "stderr must be captured, not discarded"


def test_a_process_flooding_stderr_still_delivers_audio(spawned):
    """
    1 MB of stderr is ~16x the pipe buffer. With the old piped stderr this
    deadlocks and no audio ever arrives.
    """
    stream = spawned(
        "import sys;"
        "sys.stderr.write('x' * 1024 * 1024);"
        "sys.stderr.flush();"
        "sys.stdout.buffer.write(b'AUDIO' * 200);"
        "sys.stdout.buffer.flush()"
    )
    data = stream.stdout.read(1000)
    assert data.startswith(b"AUDIO"), "the process stalled writing to stderr"


def test_stdout_still_applies_backpressure(spawned):
    """
    stdout must stay a pipe. That is what bounds memory: yt-dlp blocks when
    FFmpeg is not consuming, instead of buffering a whole track in RAM.
    """
    stream = spawned("import sys; sys.stdout.buffer.write(b'A' * 64); sys.stdout.buffer.flush()")
    assert stream.stdout is not None
    assert stream.stdout.read(64) == b"A" * 64


# -- reaping -----------------------------------------------------------

def test_close_reaps_the_process_leaving_no_zombie(spawned):
    """
    The live deployment accumulated a yt-dlp zombie that survived 19 days
    because the child was killed but never waited on.
    """
    stream = spawned("import time; time.sleep(60)")
    assert stream._proc.poll() is None, "the process should still be running"

    stream.close()

    assert stream._proc.poll() is not None, "close() must reap the child, not just kill it"
    assert stream._proc.returncode is not None


def test_close_is_idempotent(spawned):
    stream = spawned("import time; time.sleep(60)")
    stream.close()
    stream.close()
    stream.close()
    assert stream._proc.poll() is not None


def test_close_on_an_already_finished_process(spawned):
    stream = spawned("pass")
    stream._proc.wait(timeout=30)   # deterministic: the child has flushed stderr
    stream.close()      # must not raise
    assert stream._proc.poll() is not None


def test_close_releases_the_pipes(spawned):
    """Leaked pipe file descriptors accumulate over a long uptime."""
    stream = spawned("import time; time.sleep(60)")
    stdout = stream.stdout
    stream.close()
    assert stdout.closed, "the stdout pipe must be closed explicitly, not left to the GC"


def test_stdout_is_none_after_close(spawned):
    stream = spawned("import time; time.sleep(60)")
    stream.close()
    assert stream.stdout is None


# -- error classification ----------------------------------------------

def test_classify_detects_the_youtube_bot_check(spawned):
    stream = spawned(
        "import sys; sys.stderr.write(\"ERROR: Sign in to confirm you are not a bot\")"
    )
    stream._proc.wait(timeout=30)   # deterministic: the child has flushed stderr
    stream.close()
    assert stream.classify_error() == "blocked"


def test_classify_reports_other_failures_as_unavailable(spawned):
    stream = spawned("import sys; sys.stderr.write('ERROR: Video unavailable')")
    stream._proc.wait(timeout=30)   # deterministic: the child has flushed stderr
    stream.close()
    assert stream.classify_error() == "unavailable"


def test_classify_works_after_close(spawned):
    """
    The player kills the stream before asking why it failed, so classification
    has to survive teardown.
    """
    stream = spawned("import sys; sys.stderr.write('please sign in to confirm')")
    stream._proc.wait(timeout=30)   # deterministic: the child has flushed stderr
    stream.close()
    assert stream.classify_error() == "blocked"
    assert stream.classify_error() == "blocked", "classification must be repeatable"


def test_classify_only_reads_the_tail_of_a_huge_stderr(spawned):
    """A megabyte of throttling noise must not be loaded whole to find a phrase."""
    stream = spawned(
        "import sys;"
        "sys.stderr.write('noise ' * 200000);"
        "sys.stderr.write('ERROR: Sign in to confirm you are not a bot')"
    )
    stream._proc.wait(timeout=30)   # deterministic: the child has flushed stderr
    stream.close()
    assert stream.classify_error() == "blocked"


def test_classify_survives_undecodable_stderr(spawned):
    stream = spawned("import sys; sys.stderr.buffer.write(b'\\xff\\xfe bad bytes')")
    stream._proc.wait(timeout=30)   # deterministic: the child has flushed stderr
    stream.close()
    assert stream.classify_error() == "unavailable"


def test_classify_before_close_still_works(spawned):
    stream = spawned("import sys; sys.stderr.write('ERROR: Private video')")
    stream._proc.wait(timeout=30)   # deterministic: the child has flushed stderr
    assert stream.classify_error() == "unavailable"


# -- command construction ----------------------------------------------

def test_spawn_stream_targets_the_track_url(monkeypatch):
    captured = {}
    monkeypatch.setattr(AudioStream, "launch", classmethod(
        lambda cls, cmd: captured.setdefault("cmd", cmd)
    ))
    media.spawn_stream({"url": "https://example.invalid/watch", "title": "T"})
    assert captured["cmd"][-1] == "https://example.invalid/watch"


def test_spawn_stream_falls_back_to_a_search_when_there_is_no_url(monkeypatch):
    captured = {}
    monkeypatch.setattr(AudioStream, "launch", classmethod(
        lambda cls, cmd: captured.setdefault("cmd", cmd)
    ))
    media.spawn_stream({"url": None, "query": "bohemian rhapsody", "title": "T"})
    assert captured["cmd"][-1] == "ytsearch1:bohemian rhapsody"


def test_spawn_stream_passes_cookies_when_configured(monkeypatch):
    captured = {}
    monkeypatch.setattr(AudioStream, "launch", classmethod(
        lambda cls, cmd: captured.setdefault("cmd", cmd)
    ))
    monkeypatch.setitem(media.YTDL_OPTIONS, "cookiefile", "/tmp/cookies.txt")
    media.spawn_stream({"url": "https://example.invalid/x", "title": "T"})
    cmd = captured["cmd"]
    assert "--cookies" in cmd
    assert cmd[cmd.index("--cookies") + 1] == "/tmp/cookies.txt"


def test_spawn_stream_omits_cookies_when_not_configured(monkeypatch):
    captured = {}
    monkeypatch.setattr(AudioStream, "launch", classmethod(
        lambda cls, cmd: captured.setdefault("cmd", cmd)
    ))
    monkeypatch.delitem(media.YTDL_OPTIONS, "cookiefile", raising=False)
    media.spawn_stream({"url": "https://example.invalid/x", "title": "T"})
    assert "--cookies" not in captured["cmd"]
