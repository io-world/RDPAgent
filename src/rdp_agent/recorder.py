"""Writes every run to runs/<id>/<timestamp>/: step screenshots, a JSONL log and an HTML report.

Runs of the same ID are consolidated in runs/<id>/: history.jsonl gets one line per run and
history.html compares them (pass/fail, total time, time per step) so you can see how a script
performs over time. The report is the audit trail / test evidence: open it in a browser.
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path

CSS = """
body{font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 16px;color:#222;background:#fafafa}
img{max-width:100%;border:1px solid #ccc;border-radius:6px} section{margin:2rem 0}
.badge{display:inline-block;padding:.2em .7em;border-radius:4px;color:#fff;font-weight:600;background:#1a7f37}
.badge.fail{background:#cf222e} .note{color:#555} code{background:#eee;padding:.1em .3em} .bad h3,td.bad{color:#cf222e}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums} th,td{padding:.35em .6em;border-bottom:1px solid #ddd;text-align:left;white-space:nowrap}
.scroll{overflow-x:auto} .bar{height:.6em;background:#0969da;border-radius:3px;min-width:2px} a{color:#0969da}
@media (prefers-color-scheme:dark){body{background:#161616;color:#ddd} code{background:#333} .note{color:#aaa} th,td{border-color:#333} a{color:#58a6ff} .bar{background:#58a6ff}}
"""


class Recorder:
    def __init__(self, runs_dir: Path, run_id: str, script_name: str, host: str):
        self.home = Path(runs_dir) / run_id  # every run of this ID lives here
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = self.home / stamp
        n = 1
        while self.dir.exists():  # two runs in the same second
            n += 1
            self.dir = self.home / f"{stamp}-{n}"
        self.dir.mkdir(parents=True)
        self.run_id, self.script_name, self.host = run_id, script_name, host
        self.steps: list[dict] = []
        self.started = time.time()
        self._last_t = 0.0
        self.result: dict | None = None
        self._log = open(self.dir / "steps.jsonl", "a", encoding="utf-8")

    def step(self, n: int, frame, note: str, action: str, result: str, ok: bool) -> None:
        img = f"step-{n:03d}.png"
        frame.save(self.dir / img)
        t = round(time.time() - self.started, 2)
        rec = {
            "step": n,
            "t": t,
            "seconds": round(t - self._last_t, 2),  # this step, including waiting for the screen to settle
            "ok": ok,
            "note": note,
            "action": action,
            "result": result,
            "screenshot": img,
        }
        self._last_t = t
        self.steps.append(rec)
        self._log.write(json.dumps(rec) + "\n")
        self._log.flush()

    def finish(self, success: bool, summary: str, final_frame=None) -> Path:
        if final_frame is not None:
            final_frame.save(self.dir / "final.png")
        self.result = {
            "success": success,
            "summary": summary,
            "seconds": round(time.time() - self.started, 1),
            "steps": len(self.steps),
        }
        (self.dir / "result.json").write_text(json.dumps(self.result, indent=2))
        self._log.close()
        self._add_to_history()
        return self._write_html(final_frame is not None)

    # ------------------------------------------------------------ history
    def _add_to_history(self) -> None:
        entry = {
            "run": self.dir.name,
            "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started)),
            "name": self.script_name,
            "host": self.host,
            **(self.result or {}),
            "step_seconds": [
                {"step": s["step"], "action": s["action"], "seconds": s["seconds"], "ok": s["ok"]} for s in self.steps
            ],
        }
        with open(self.home / "history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        write_history_html(self.home)

    def _write_html(self, has_final: bool) -> Path:
        r = self.result or {}
        esc = html.escape
        rows = "\n".join(
            f"""<section class="{"" if s["ok"] else "bad"}"><h3>Step {s["step"]} <small>+{s["t"]}s ({s["seconds"]}s)</small></h3>
<p class=note>{esc(s["note"])}</p>
<p><code>{esc(s["action"])}</code> &rarr; {esc(s["result"])}</p>
<img src="{s["screenshot"]}" loading=lazy alt="screen before step {s["step"]}"></section>"""
            for s in self.steps
        )
        final = '<section><h3>Final screen</h3><img src="final.png"></section>' if has_final else ""
        badge = "PASS" if r.get("success") else "FAIL"
        page = f"""<!doctype html><meta charset=utf-8><title>RDP Agent: {esc(self.script_name)}</title>
<style>{CSS}</style>
<p><a href="../history.html">&larr; all runs of {esc(self.run_id)}</a></p>
<h1>{esc(self.script_name)} <span class="badge{"" if r.get("success") else " fail"}">{badge}</span></h1>
<p><b>Host:</b> {esc(self.host)} &middot; <b>Steps:</b> {r.get("steps")} &middot; <b>Time:</b> {r.get("seconds")}s</p>
<p><b>Result:</b> {esc(r.get("summary", ""))}</p>
{rows}
{final}"""
        out = self.dir / "report.html"
        out.write_text(page, encoding="utf-8")
        return out


def load_history(home: Path) -> list[dict]:
    path = Path(home) / "history.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_history_html(home: Path, step_columns: int = 10) -> Path:
    """runs/<id>/history.html: every run of one ID, newest first, plus time per step for the latest runs."""
    esc = html.escape
    runs = load_history(home)
    run_id = Path(home).name
    passed = sum(r["success"] for r in runs)
    times = [r["seconds"] for r in runs]
    longest = max(times, default=1) or 1

    run_rows = "\n".join(
        f"""<tr><td>{esc(r["started"])}</td>
<td><span class="badge{"" if r["success"] else " fail"}">{"PASS" if r["success"] else "FAIL"}</span></td>
<td>{r["seconds"]}s</td><td style="width:30%"><div class=bar style="width:{r["seconds"] / longest * 100:.0f}%"></div></td>
<td>{r["steps"]}</td><td><a href="{esc(r["run"])}/report.html">report</a></td>
<td class="{"" if r["success"] else "bad"}">{esc(r["summary"])}</td></tr>"""
        for r in reversed(runs)
    )

    recent = runs[-step_columns:]  # oldest -> newest, left to right
    n_steps = max((len(r["step_seconds"]) for r in recent), default=0)
    step_rows = []
    for i in range(n_steps):
        latest = next(r["step_seconds"][i] for r in reversed(recent) if i < len(r["step_seconds"]))
        cells = []
        for r in recent:
            s = r["step_seconds"][i] if i < len(r["step_seconds"]) else None
            cells.append("<td></td>" if s is None else f'<td class="{"" if s["ok"] else "bad"}">{s["seconds"]}</td>')
        step_rows.append(f"<tr><td>{i + 1}</td><td><code>{esc(latest['action'][:60])}</code></td>{''.join(cells)}</tr>")
    step_head = "".join(f"<th>{esc(r['started'][5:19])}</th>" for r in recent)

    stats = (
        f"<b>Runs:</b> {len(runs)} &middot; <b>Passed:</b> {passed}/{len(runs)} &middot; "
        f"<b>Last:</b> {times[-1]}s &middot; <b>Best:</b> {min(times)}s &middot; "
        f"<b>Average:</b> {sum(times) / len(times):.1f}s"
        if runs
        else "No runs yet."
    )
    page = f"""<!doctype html><meta charset=utf-8><title>RDP Agent: {esc(run_id)} history</title>
<style>{CSS}</style>
<h1>{esc(run_id)} <small>run history</small></h1>
<p>{stats}</p>
<h2>Runs</h2>
<div class=scroll><table><tr><th>Started</th><th>Result</th><th>Time</th><th></th><th>Steps</th><th></th><th>Summary</th></tr>
{run_rows}</table></div>
<h2>Seconds per step <small>(last {len(recent)} runs)</small></h2>
<div class=scroll><table><tr><th>#</th><th>Step</th>{step_head}</tr>
{"".join(step_rows)}</table></div>"""
    out = Path(home) / "history.html"
    out.write_text(page, encoding="utf-8")
    return out
