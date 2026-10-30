# RDP Agent

Runs scripted steps on a remote Windows computer over RDP (click, type, press keys,
wait, screenshot) and writes an HTML report with a screenshot of every step.
**No AI yet**: you write the steps; the agent executes them reliably and records the evidence.

- **Eyes and hands:** [PythonWrapFreeRDP](../PythonWrapFreeRDP)
- **Scripts:** small TOML files in `scripts/`
- **Evidence:** `runs/<timestamp>/report.html`: PASS/FAIL, each step's action, result and screenshot

## Setup

Needs the wrapper checked out next to this folder (it is an editable path dependency, `../PythonWrapFreeRDP`),
with FreeRDP built there (see its README).

```bash
git clone https://github.com/io-world/PythonWrapFreeRDP.git
git clone https://github.com/io-world/RDPAgent.git
cd RDPAgent
cp .env.example .env               # fill in RDP_HOST / RDP_USER / RDP_PASSWORD
uv sync
uv run rdp-agent --fingerprint     # prints the PC's certificate fingerprint -> RDP_CERT_FINGERPRINT in .env
uv run rdp-agent --screenshot      # sanity check: saves runs/screenshot.png
```

## Use

```bash
uv run rdp-agent scripts/notepad.toml          # run, then the report opens in your browser
uv run rdp-agent --step scripts/notepad.toml   # approve each step (Enter=run, s=skip, q=quit)
uv run rdp-agent --check scripts/notepad.toml  # validate a script without connecting
uv run rdp-agent --keep-going my.toml          # don't stop at the first failed step
```

## Step files and run history

Each job is a JSON file in `steps/`, named by its ID (`steps/<id>.json`):

```json
{"name": "Open Notepad", "steps": [{"action": "press_keys", "keys": ["win", "r"]}, {"action": "type_text", "text": "notepad\n"}]}
```

```bash
uv run python main.py demo        # runs steps/demo.json (settings at the top of main.py)
uv run python main.py             # lists the IDs
uv run python main.py cursor-blink-off      # once per sign-in: stop text cursors blinking (steps settle faster)
uv run python main.py cursor-blink-restore  # back to the Windows default
uv run python main.py demo --live           # watch it live at http://127.0.0.1:8765/ (this machine only)
```

`--live` streams the screen while the run goes: a background thread waits for the screen to change,
grabs at most `LIVE_FPS` frames a second and JPEG-encodes them (`LIVE_QUALITY`, `LIVE_SCALE` in `main.py`).
The page fits the screen to the window; click it for actual size (and click again to fit). VLC / OpenCV
can open the plain MJPEG stream at `http://127.0.0.1:8765/stream`.
Stream statistics (frame rate, encode time, frame size, bandwidth, CPU) are printed and saved as
`live.json` in the run folder.

Every run of an ID is kept together, so you can see how it performs over time:

```
runs/<id>/
  history.html       # all runs: pass/fail, total time, seconds per step side by side
  history.jsonl      # one line per run (same data, for scripts)
  <timestamp>/       # one run: report.html, step screenshots, steps.jsonl, result.json
```

`rdp-agent` accepts these JSON files too, and TOML scripts are grouped under `runs/<file name>/` the same way.

## Writing a script

```toml
name = "Notepad smoke test"

[[step]]
action = "press_keys"
keys = ["win", "r"]
note = "open the Run dialog"     # optional, shown in the report

[[step]]
action = "expect_change"          # fails the run if the previous step changed nothing

[[step]]
action = "type_text"
text = "notepad\n"                # \n presses Enter
```

| action | arguments |
|---|---|
| `click` | `x`, `y`, optional `button` (left/right/middle), `double` |
| `double_click`, `right_click` | `x`, `y` |
| `move` | `x`, `y` |
| `type_text` | `text` (Unicode; `\n` = Enter, `\t` = Tab) |
| `type_secret` | `name` (e.g. `password` = `RDP_PASSWORD`, or any `RDP_SECRET_<NAME>`), optional `press_enter` |
| `press_keys` | `keys` (one key or a combo like `["ctrl","shift","esc"]`), optional `times` |
| `scroll` | `amount` (+up / -down), optional `x`, `y` |
| `drag` | `x1`, `y1`, `x2`, `y2` |
| `wait` | `seconds` |
| `wait_for_idle` | optional `quiet` (default 1s), `timeout` (default 30s) |
| `expect_change` | optional `timeout`: fails if the previous step didn't change the screen |
| `screenshot` | optional `name` (saved in the run folder) |

Every step automatically waits for the screen to settle before the next one runs.
Coordinates are pixels on the remote screen (1920x1080 by default, set with `RDP_WIDTH`/`RDP_HEIGHT` or `WIDTH, HEIGHT` in `main.py`; take a `--screenshot`
and read positions off it).

## Safety
- Passwords live in `.env` (git-ignored), never on the command line, never in logs or reports.
- `--step` lets you approve every action.
- Certificate pinning via `RDP_CERT_FINGERPRINT`; `RDP_IGNORE_CERT=1` only on your own LAN.

## Later: adding AI
Steps use the same shape an AI planner would produce (`click`, `type_text`, `press_keys`...).
Adding AI means generating these steps from a goal and a screenshot. The runner, actions,
secrets handling and reports stay as they are.

## Tests
```bash
uv run pytest -q     # unit tests + a real run against the wrapper's local RDP test server
```
