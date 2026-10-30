"""The steps a script can use. Each one is a small function on a pythonwrapfreerdp Session.

Names and arguments are deliberately the same shape an AI planner would use later
(click / type_text / press_keys / ...), so adding AI means generating these steps,
not rewriting the agent.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

Action = Callable[[Any, dict, "Context"], str]


class Context:
    """What actions can reach besides the session: secrets and the screenshot saver."""

    def __init__(self, secrets: dict[str, str], save_screenshot: Callable[[str], str], type_interval: float = 0.0):
        self.secrets = secrets
        self.save_screenshot = save_screenshot
        self.type_interval = type_interval  # pause between typed characters
        self.seq_before = 0  # frame seq before the current step
        self.seq_before_prev = 0  # frame seq before the previous step (used by expect_change)


class StepError(Exception):
    """A step was written wrong or failed its check."""


def _need(args: dict, *keys: str) -> None:
    missing = [k for k in keys if k not in args]
    if missing:
        raise StepError(f"missing {', '.join(missing)}")


def click(s, a, ctx):
    _need(a, "x", "y")
    button = a.get("button", "left")
    if a.get("double"):
        s.double_click(a["x"], a["y"], button=button)
        return f"double-clicked {button} at {a['x']},{a['y']}"
    s.click(a["x"], a["y"], button=button)
    return f"clicked {button} at {a['x']},{a['y']}"


def double_click(s, a, ctx):
    return click(s, {**a, "double": True}, ctx)


def right_click(s, a, ctx):
    return click(s, {**a, "button": "right"}, ctx)


def move(s, a, ctx):
    _need(a, "x", "y")
    s.mouse_move(a["x"], a["y"])
    return f"moved mouse to {a['x']},{a['y']}"


def type_text(s, a, ctx):
    _need(a, "text")
    s.type(a["text"], interval=ctx.type_interval)
    return f"typed {len(a['text'])} characters"


def type_secret(s, a, ctx):
    _need(a, "name")
    name = a["name"].lower()
    if name not in ctx.secrets:
        raise StepError(
            f"no secret named {name!r} (set RDP_SECRET_{name.upper()} in .env); have: {sorted(ctx.secrets)}"
        )
    s.type(ctx.secrets[name], interval=ctx.type_interval)
    if a.get("press_enter"):
        s.press("enter")
    return f"typed secret <{name}>" + (" + enter" if a.get("press_enter") else "")


def press_keys(s, a, ctx):
    _need(a, "keys")
    keys = a["keys"] if isinstance(a["keys"], list) else [a["keys"]]
    if len(keys) == 1:
        s.press(keys[0], presses=int(a.get("times", 1)))
    else:
        s.hotkey(*keys)
    return f"pressed {'+'.join(keys)}"


def scroll(s, a, ctx):
    _need(a, "amount")
    s.scroll(int(a["amount"]), a.get("x"), a.get("y"))
    return f"scrolled {a['amount']}"


def drag(s, a, ctx):
    _need(a, "x1", "y1", "x2", "y2")
    s.drag((a["x1"], a["y1"]), (a["x2"], a["y2"]))
    return f"dragged {a['x1']},{a['y1']} -> {a['x2']},{a['y2']}"


def wait(s, a, ctx):
    _need(a, "seconds")
    time.sleep(float(a["seconds"]))
    return f"waited {a['seconds']}s"


def wait_for_idle(s, a, ctx):
    quiet, timeout = float(a.get("quiet", 1.0)), float(a.get("timeout", 30))
    if not s.wait_for_idle(quiet=quiet, timeout=timeout):
        raise StepError(f"screen did not settle within {timeout}s")
    return f"screen settled ({quiet}s quiet)"


def expect_change(s, a, ctx):
    """Fails unless the screen changed since just before the previous step (i.e. that step did something),
    waiting up to `timeout` seconds for it."""
    timeout = float(a.get("timeout", 5))
    if not s.wait_for_change(timeout=timeout, after=ctx.seq_before_prev):
        raise StepError(f"screen did not change within {timeout}s")
    return "screen changed"


def screenshot(s, a, ctx):
    name = a.get("name", "screenshot")
    return f"saved {ctx.save_screenshot(name)}"


ACTIONS: dict[str, Action] = {
    f.__name__: f
    for f in (
        click, double_click, right_click, move, type_text, type_secret, press_keys,
        scroll, drag, wait, wait_for_idle, expect_change, screenshot,
    )
}  # fmt: skip


def describe(step: dict) -> str:
    """One-line description for logs and the report. Never shows secret values."""
    action = step.get("action", "?")
    args = {k: v for k, v in step.items() if k not in ("action", "note")}
    if action == "type_text":
        return f"type_text {args.get('text', '')!r}"
    inner = ", ".join(f"{k}={v}" for k, v in args.items())
    return f"{action}({inner})"
