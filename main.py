"""Run the steps in steps/<id>.json on the remote desktop and add the run to that ID's history.

    uv run python main.py demo            # run steps/demo.json, then open the report
    uv run python main.py demo --step     # approve each step (Enter=run, s=skip, q=quit)
    uv run python main.py demo --no-open  # don't open the report
    uv run python main.py demo --live     # also stream the screen to http://127.0.0.1:8765/ while it runs
    uv run python main.py                 # list the IDs in steps/
    uv run python main.py cursor-blink-off  # once per sign-in: stop text cursors blinking (faster settling)

Each run lands in runs/<id>/<timestamp>/; runs/<id>/history.html compares all runs of that ID.
Coordinates in the step files are pixels on the remote screen (take `uv run rdp-agent --screenshot`).
"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path

from rdp_agent import Config, Runner, Script, StepError
from rdp_agent.cli import _confirm
from rdp_agent.live import LiveStream

# --- general configuration
STEP_DELAY = 0.8  # seconds the screen must stay still after a step before the next one runs
SETTLE_TIMEOUT = 3.0  # stop waiting for a still screen after this many seconds and move on
REPORT_UNSETTLED = True  # note in the log/report when a step's screen never went still
TYPE_INTERVAL = 0.01  # seconds between typed characters; at 0, long text can arrive garbled
WIDTH, HEIGHT = 1920, 1080  # remote desktop resolution (step coordinates are in these pixels)
# --live: watch the run in a browser (MJPEG over HTTP, local machine only)
LIVE_PORT = 8765
LIVE_FPS = 10  # at most this many frames a second; an unchanged screen sends nothing
LIVE_QUALITY = 80  # JPEG quality 1-95: lower = smaller frames, blurrier text
LIVE_SCALE = 1.0  # 0.5 = stream at half size (1/4 the pixels)
STEPS_DIR = Path("steps")  # one <id>.json per script; runs of an ID are grouped in runs/<id>/


def main(argv: list[str]) -> int:
    os.chdir(Path(__file__).resolve().parent)  # .env, steps/ and runs/ live next to this file
    ids = [a for a in argv if not a.startswith("--")]
    if not ids:
        available = ", ".join(sorted(p.stem for p in STEPS_DIR.glob("*.json"))) or "none"
        print(f"usage: main.py <id> [--step] [--no-open]\navailable IDs: {available}")
        return 2
    try:
        script = Script.load(STEPS_DIR / f"{ids[0]}.json")
    except (StepError, OSError, ValueError) as e:
        print(f"steps error: {e}")
        return 2
    cfg = Config.from_env(
        settle_seconds=STEP_DELAY,
        settle_timeout=SETTLE_TIMEOUT,
        report_unsettled=REPORT_UNSETTLED,
        type_interval=TYPE_INTERVAL,
        width=WIDTH,
        height=HEIGHT,
    )
    runner = Runner(cfg, confirm=_confirm if "--step" in argv else None)
    if "--live" in argv:
        with runner.connect() as session, LiveStream(session, LIVE_PORT, LIVE_FPS, LIVE_QUALITY, LIVE_SCALE) as live:
            print(f"live view: {live.url}")
            if "--no-open" not in argv:
                webbrowser.open(live.url)
            result = runner.run(script, session=session)
        stats = live.summary()
        (result.report.parent / "live.json").write_text(json.dumps(stats, indent=2))
        print("live stream:", json.dumps(stats))
    else:
        result = runner.run(script)
    print(f"\n{'PASS' if result.success else 'FAIL'}: {result.summary}")
    print(f"report:  {result.report}")
    print(f"history: {result.history}")
    if "--no-open" not in argv:
        webbrowser.open(result.report.resolve().as_uri())
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
