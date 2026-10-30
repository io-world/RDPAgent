"""Tests: script loading, every action against a fake session, and a real run against an RDP server."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from rdp_agent.actions import ACTIONS, StepError, describe
from rdp_agent.config import Config
from rdp_agent.runner import Runner, Script

ROOT = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------------ fakes


class FakeFrame:
    def __init__(self, seq=1):
        self.seq = seq

    def save(self, path):
        Path(path).write_bytes(b"\x89PNG fake")


class FakeSession:
    def __init__(self, change=True, idle=True):
        self.calls = []
        self.change = change
        self.idle = idle
        self.idle_timeouts = []
        self.size = (1280, 800)

    def frame(self):
        return FakeFrame()

    def wait_for_idle(self, quiet=1.0, timeout=30):
        self.idle_timeouts.append(timeout)
        return self.idle

    def wait_for_change(self, timeout=10, after=None):
        return self.change

    def save_screenshot(self, path):
        Path(path).write_bytes(b"\x89PNG fake")
        return path

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a, k))

        return rec


@pytest.fixture
def cfg(tmp_path):
    return Config(host="test", runs_dir=tmp_path / "runs", settle_seconds=0.01, secrets={"password": "hunter2"})


def quiet(*a):
    pass


# ------------------------------------------------------------------ unit tests


@pytest.mark.parametrize("name", ["notepad", "login", "screenshot"])
def test_bundled_scripts_are_valid(name):
    s = Script.load(ROOT / "scripts" / f"{name}.toml")
    assert s.steps and s.name


def test_bundled_step_files_are_valid():
    files = sorted((ROOT / "steps").glob("*.json"))
    assert files
    for f in files:
        s = Script.load(f)
        assert s.id == f.stem and s.steps


def test_json_steps_load_as_object_or_list(tmp_path):
    (tmp_path / "abc-123.json").write_text('{"name": "N", "steps": [{"action": "wait", "seconds": 0}]}')
    (tmp_path / "bare.json").write_text('[{"action": "wait", "seconds": 0}]')
    (tmp_path / "bad.json").write_text('{"steps": ["click"]}')
    a, b = Script.load(tmp_path / "abc-123.json"), Script.load(tmp_path / "bare.json")
    assert (a.id, a.name, len(a.steps)) == ("abc-123", "N", 1)
    assert (b.id, b.name) == ("bare", "bare")
    with pytest.raises(StepError, match="expected an object"):
        Script.load(tmp_path / "bad.json")


def test_runs_of_the_same_id_build_a_history(cfg):
    ok = Script("x", [{"action": "click", "x": 1, "y": 1}], id="job-7")
    bad = Script("x", [{"action": "click", "x": 1}], id="job-7")
    results = [Runner(cfg, log=quiet).run(s, session=FakeSession()) for s in (ok, bad, ok)]
    home = cfg.runs_dir / "job-7"
    assert all(r.history == home / "history.html" and r.report.parent.parent == home for r in results)
    assert len({r.report.parent for r in results}) == 3
    lines = [json.loads(x) for x in (home / "history.jsonl").read_text().splitlines()]
    assert [x["success"] for x in lines] == [True, False, True]
    assert lines[0]["step_seconds"][0]["action"] == "click(x=1, y=1)"
    page = (home / "history.html").read_text()
    assert "Passed:</b> 2/3" in page and page.count("/report.html") == 3


def test_unknown_action_is_rejected_at_load(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('[[step]]\naction = "teleport"\n')
    with pytest.raises(StepError, match="unknown action"):
        Script.load(p)


def test_all_actions_drive_the_session(cfg):
    steps = [
        {"action": "click", "x": 1, "y": 2},
        {"action": "double_click", "x": 3, "y": 4},
        {"action": "right_click", "x": 5, "y": 6},
        {"action": "move", "x": 7, "y": 8},
        {"action": "type_text", "text": "hi"},
        {"action": "type_secret", "name": "password", "press_enter": True},
        {"action": "press_keys", "keys": ["ctrl", "s"]},
        {"action": "press_keys", "keys": ["tab"], "times": 3},
        {"action": "scroll", "amount": -2},
        {"action": "drag", "x1": 0, "y1": 0, "x2": 9, "y2": 9},
        {"action": "wait", "seconds": 0.01},
        {"action": "wait_for_idle"},
        {"action": "expect_change"},
        {"action": "screenshot", "name": "shot"},
    ]
    assert {s["action"] for s in steps} == set(ACTIONS), "test should cover every action"
    sess = FakeSession()
    res = Runner(cfg, log=quiet).run(Script("all", steps), session=sess)
    assert res.success, res.summary
    names = [c[0] for c in sess.calls]
    assert names == ["click", "double_click", "click", "mouse_move", "type", "type", "press",
                     "hotkey", "press", "scroll", "drag"]  # fmt: skip
    assert ("type", ("hunter2",), {"interval": 0.0}) in sess.calls
    run = Path(res.report).parent
    assert (run / "01-shot.png").exists() and (run / "final.png").exists()
    assert len((run / "steps.jsonl").read_text().splitlines()) == len(steps)
    report = (run / "report.html").read_text()
    assert "PASS" in report and "hunter2" not in report
    assert "hunter2" not in (run / "steps.jsonl").read_text()


def test_failure_stops_run_and_is_reported(cfg):
    steps = [
        {"action": "click", "x": 1, "y": 1},
        {"action": "expect_change", "timeout": 0.01},
        {"action": "click", "x": 2, "y": 2},
    ]
    sess = FakeSession(change=False)
    res = Runner(cfg, log=quiet).run(Script("fails", steps), session=sess)
    assert not res.success and res.steps_run == 2
    assert "did not change" in res.summary
    assert len(sess.calls) == 1  # third step never ran
    assert json.loads((Path(res.report).parent / "result.json").read_text())["success"] is False


def test_keep_going_runs_everything(cfg):
    steps = [{"action": "type_secret", "name": "nope"}, {"action": "click", "x": 1, "y": 1}]
    sess = FakeSession()
    res = Runner(cfg, log=quiet).run(Script("k", steps), session=sess, stop_on_error=False)
    assert not res.success and "no secret named" in res.summary
    assert sess.calls[-1][0] == "click"


def test_missing_argument_is_a_clear_failure(cfg):
    res = Runner(cfg, log=quiet).run(Script("m", [{"action": "click", "x": 1}]), session=FakeSession())
    assert not res.success and "missing y" in res.summary


def test_operator_can_skip(cfg):
    sess = FakeSession()
    res = Runner(cfg, confirm=lambda d: False, log=quiet).run(
        Script("skip", [{"action": "click", "x": 1, "y": 1}]), session=sess
    )
    assert res.success and sess.calls == []


@pytest.mark.parametrize("report", [True, False])
def test_unsettled_screen_is_noted_not_failed(cfg, report):
    cfg.settle_timeout, cfg.report_unsettled = 2.5, report
    sess = FakeSession(idle=False)
    res = Runner(cfg, log=quiet).run(Script("u", [{"action": "click", "x": 1, "y": 1}]), session=sess)
    assert res.success and sess.idle_timeouts[-1] == 2.5
    log = (Path(res.report).parent / "steps.jsonl").read_text()
    assert ("screen still changing after 2.5s" in log) is report


def test_live_stream_serves_jpeg_frames():
    import urllib.request

    from pythonwrapfreerdp import Frame

    from rdp_agent.live import LiveStream

    class Screen:
        seq = 1

        def wait_for_change(self, timeout=10, after=None):
            if self.seq > after:
                return True
            time.sleep(timeout)
            return False

        def frame(self):
            return Frame(64, 32, 256, self.seq, bytes(range(256)) * 32)

    with LiveStream(Screen(), port=0, fps=50) as live:
        live.poll_timeout = 0.3
        assert urllib.request.urlopen(live.url).read().startswith(b"<!doctype html>")
        with urllib.request.urlopen(live.url + "frame.jpg?after=0", timeout=5) as r:
            assert r.headers["X-Frame"] == "1" and r.read().startswith(b"\xff\xd8")
        with urllib.request.urlopen(live.url + "frame.jpg?after=1", timeout=5) as r:
            assert r.status == 204  # nothing newer arrived within poll_timeout
        with urllib.request.urlopen(live.url + "stream", timeout=5) as r:
            assert r.headers["Content-Type"].startswith("multipart/x-mixed-replace")
            body = b""
            while body.count(b"--frame") < 2:  # the first frame must be closed right away, not by the next one
                body += r.read1(65536)
    assert b"Content-Type: image/jpeg" in body and b"\xff\xd8" in body  # JPEG start marker
    assert body.rstrip().endswith(b"--frame")
    stats = live.summary()
    assert stats["frames_encoded"] >= 1 and stats["screen_size"] == "64x32" and stats["viewers"] == 2


def test_default_resolution_is_1080p():
    assert (Config(host="h").width, Config(host="h").height) == (1920, 1080)


def test_describe_never_shows_secrets():
    assert "hunter2" not in describe({"action": "type_secret", "name": "password"})
    assert describe({"action": "type_text", "text": "hi"}) == "type_text 'hi'"


def test_config_reads_env_and_secrets(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("RDP_HOST=1.2.3.4\nRDP_USER='me'\nRDP_PASSWORD=\"p w\"\nRDP_SECRET_PIN=1234\n")
    for k in ("RDP_HOST", "RDP_USER", "RDP_PASSWORD", "RDP_SECRET_PIN"):
        monkeypatch.delenv(k, raising=False)
    c = Config.from_env()
    assert (c.host, c.user, c.password) == ("1.2.3.4", "me", "p w")
    assert c.secrets == {"pin": "1234", "password": "p w"}


def test_no_ai_dependencies():
    deps = (ROOT / "pyproject.toml").read_text().lower()
    assert "anthropic" not in deps and "openai" not in deps


# ------------------------------------------------- end to end with a real RDP server

WRAPPER = ROOT.parent / "PythonWrapFreeRDP"


def _server_bin():
    vf = WRAPPER / "FREERDP_VERSION"
    if not vf.exists():
        return None
    marker = WRAPPER / ".freerdp" / vf.read_text().strip() / "test-server"
    return Path(marker.read_text().strip()) if marker.exists() else None


@pytest.fixture
def sample_server(tmp_path):
    exe = _server_bin()
    if not exe or not exe.exists():
        pytest.skip(
            "build the wrapper's test server: uv run scripts/build_freerdp.py --test-server (in PythonWrapFreeRDP)"
        )
    for f in (WRAPPER / "tests" / "assets").iterdir():
        shutil.copy(f, tmp_path / f.name)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [str(exe), "--cert=test-server.crt", "--key=test-server.key", f"--port={port}"],
        cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield port
    proc.terminate()
    proc.wait(5)


def test_end_to_end_against_real_rdp_server(sample_server, cfg):
    """Real RDP: moving the mouse makes the sample server draw an icon, which expect_change catches."""
    cfg.host, cfg.port, cfg.ignore_cert, cfg.user, cfg.password = "127.0.0.1", sample_server, True, "u", "p"
    cfg.width, cfg.height = 1024, 768
    script = Script(
        "e2e",
        [
            {"action": "screenshot", "name": "before"},
            {"action": "move", "x": 500, "y": 400},
            {"action": "expect_change"},
            {"action": "type_text", "text": "hello\n"},
            {"action": "press_keys", "keys": ["ctrl", "a"]},
            {"action": "screenshot", "name": "after"},
        ],
    )
    res = Runner(cfg, log=quiet).run(script)
    assert res.success, res.summary
    run = Path(res.report).parent
    from PIL import Image

    first = Image.open(run / "step-001.png").convert("RGB")
    assert first.getpixel((5, 5)) != (255, 255, 255), "looked before the first paint"
    final = Image.open(run / "final.png").convert("RGB")
    bg = final.getpixel((5, 5))
    # sample server draws its icon at (mouse_x+10, mouse_y)
    assert any(final.getpixel((510 + dx, 400 + dy)) != bg for dx in range(0, 30, 3) for dy in range(0, 60, 3))
