# CLAUDE.md: RDP Agent

A scripted (no AI) agent on top of `pythonwrapfreerdp` (sibling folder `../PythonWrapFreeRDP`,
linked as an editable uv path dependency, so wrapper changes are live here).

## Layout
- `src/rdp_agent/actions.py`: every step type is one small function `(session, args, ctx) -> str`,
  registered in `ACTIONS`. Raise `StepError` for bad args or failed checks.
- `runner.py`: `Script.load()` (TOML, validated up front) and `Runner.run()`: connect, run steps,
  settle the screen between steps, record everything, stop at the first failure unless told otherwise.
- `recorder.py`: `runs/<ts>/` step PNGs, named screenshots, `steps.jsonl`, `result.json`, `report.html`.
- `config.py`: connection + secrets from env/.env. `cli.py`: `rdp-agent` entry point.
- `scripts/*.toml`: example scripts (kept valid by a test).

## Rules
1. **No AI dependencies in this project for now** (a test enforces it). When AI comes, it should
   *produce steps* in the existing format; don't fork the runner.
2. Secrets never appear in logs, reports or `describe()`: only `type_secret(name)`. Tested.
3. No credentials on the command line; `.env` only.
4. New action = function in `actions.py` + add it to `ACTIONS` + a row in the README table +
   cover it in `test_all_actions_drive_the_session`.
5. RDP-level gaps get fixed in PythonWrapFreeRDP, not worked around here.
6. uv for everything: `uv sync`, `uv run pytest -q`, `uv run ruff check . && uv run ruff format .`

## Ideas / next
- Image-based checks without AI: `expect_pixel` (color at x,y), `expect_region_matches` (compare
  a crop to a saved reference PNG), `click_image` (template matching).
- Variables in scripts (`${today}`, values from .env).
- Detect the Windows "user name or password is incorrect" screen as a clear failure.
- Run a folder of scripts and produce one summary report.
