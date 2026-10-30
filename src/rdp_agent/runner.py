"""Runs a script (a list of steps) against a remote desktop and records everything.

A script is a JSON file named after its ID, e.g. steps/demo.json:

    {"name": "Open Notepad", "steps": [{"action": "press_keys", "keys": ["win", "r"]}, ...]}

(a bare list of steps also works), or a TOML file:

    name = "Open Notepad"

    [[step]]
    action = "press_keys"
    keys = ["win", "r"]
    note = "open the Run dialog"      # optional, shown in the report

    [[step]]
    action = "type_text"
    text = "notepad\\n"
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pythonwrapfreerdp as rdp

from .actions import ACTIONS, Context, StepError, describe
from .config import Config
from .recorder import Recorder


@dataclass
class Script:
    name: str
    steps: list[dict]
    path: Path | None = None
    id: str = ""  # runs of the same ID are grouped under runs/<id>/; defaults to the file name

    def __post_init__(self):
        if not self.id:
            self.id = self.path.stem if self.path else re.sub(r"[^\w.-]+", "-", self.name).strip("-") or "script"

    @classmethod
    def load(cls, path: str | Path) -> Script:
        p = Path(path)
        if p.suffix == ".json":
            data = json.loads(p.read_text())
            if isinstance(data, list):
                data = {"steps": data}
            steps = data.get("steps")
        else:
            data = tomllib.loads(p.read_text())
            steps = data.get("step")
        if not steps or not isinstance(steps, list):
            raise StepError(f"{p}: no steps")
        script = cls(data.get("name", p.stem), steps, p)
        script.validate()
        return script

    def validate(self) -> None:
        for i, st in enumerate(self.steps, 1):
            if not isinstance(st, dict):
                raise StepError(f'step {i}: expected an object like {{"action": ...}}, got {st!r}')
            action = st.get("action")
            if action not in ACTIONS:
                raise StepError(f"step {i}: unknown action {action!r}; choose from {sorted(ACTIONS)}")


@dataclass
class RunResult:
    success: bool
    summary: str
    steps_run: int
    report: Path
    failures: list[str] = field(default_factory=list)
    history: Path | None = None  # runs/<id>/history.html: every run of this script ID


ConfirmFn = Callable[[str], bool]


class Runner:
    def __init__(self, cfg: Config, confirm: ConfirmFn | None = None, log=print):
        self.cfg = cfg
        self.confirm = confirm
        self.log = log

    def connect(self) -> rdp.Session:
        c = self.cfg
        self.log(f"connecting to {c.host}:{c.port} ...")
        s = rdp.Session(
            c.host, c.port, user=c.user, password=c.password, domain=c.domain, width=c.width, height=c.height,
            cert_fingerprint=c.cert_fingerprint, ignore_cert=c.ignore_cert,
        )  # fmt: skip
        self.log(f"connected, screen {s.size[0]}x{s.size[1]}")
        return s

    def run(self, script: Script, session: rdp.Session | None = None, stop_on_error: bool = True) -> RunResult:
        own = session is None
        s = session or self.connect()
        try:
            return self._run(script, s, stop_on_error)
        finally:
            if own:
                s.close()

    def _run(self, script: Script, s: rdp.Session, stop_on_error: bool) -> RunResult:
        rec = Recorder(self.cfg.runs_dir, script.id, script.name, self.cfg.host)
        shots = {"n": 0}

        def save_named(name: str) -> str:
            shots["n"] += 1
            path = rec.dir / f"{shots['n']:02d}-{name}.png"
            s.save_screenshot(str(path))
            return path.name

        ctx = Context(self.cfg.secrets, save_named, self.cfg.type_interval)
        s.wait_for_idle(quiet=self.cfg.settle_seconds, timeout=self.cfg.settle_timeout)
        failures: list[str] = []
        n = 0
        for n, step in enumerate(script.steps, 1):
            action = step["action"]
            desc = describe(step)
            self.log(f"[{n}/{len(script.steps)}] {desc}")
            before = s.frame()
            ctx.seq_before_prev, ctx.seq_before = (ctx.seq_before if n > 1 else before.seq), before.seq
            if self.confirm and not self.confirm(desc):
                rec.step(n, before, step.get("note", ""), desc, "skipped by operator", ok=True)
                continue
            try:
                result = ACTIONS[action](s, step, ctx)
                ok = True
            except StepError as e:
                result, ok = f"FAILED: {e}", False
            except rdp.ConnectionLost:
                rec.step(n, before, step.get("note", ""), desc, "FAILED: connection lost", ok=False)
                failures.append(f"step {n}: connection lost")
                break
            # let the UI settle before the next step (not needed after waits/screenshots)
            if (ok or not stop_on_error) and action not in ("wait", "wait_for_idle", "screenshot", "expect_change"):
                timeout = self.cfg.settle_timeout
                if not s.wait_for_idle(quiet=self.cfg.settle_seconds, timeout=timeout) and self.cfg.report_unsettled:
                    result += f"; screen still changing after {timeout:g}s"
                    self.log(f"    screen still changing after {timeout:g}s, moving on")
            rec.step(n, before, step.get("note", ""), desc, result, ok=ok)
            if not ok:
                self.log(f"    {result}")
                failures.append(f"step {n}: {result}")
                if stop_on_error:
                    break

        success = not failures
        summary = f"all {n} steps passed" if success else "; ".join(failures)
        report = rec.finish(success, summary, s.frame())
        return RunResult(success, summary, n, report, failures, rec.home / "history.html")
