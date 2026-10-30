"""Command line.

    uv run rdp-agent scripts/notepad.toml          # run a script, open the report
    uv run rdp-agent --step scripts/notepad.toml   # approve each step (Enter=run, s=skip, q=quit)
    uv run rdp-agent --check scripts/notepad.toml  # validate a script without connecting
    uv run rdp-agent --screenshot                  # just connect and save a screenshot
    uv run rdp-agent --fingerprint                 # show the server certificate fingerprint to pin

Connection details and secrets come from .env (see .env.example), never from the command line.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

import pythonwrapfreerdp as rdp

from .actions import StepError
from .config import Config
from .runner import Runner, Script


def _confirm(desc: str) -> bool:
    ans = input(f"    run {desc}?  [Enter=yes / s=skip / q=quit] ").strip().lower()
    if ans == "q":
        raise KeyboardInterrupt
    return ans != "s"


def _show_fingerprint(cfg: Config) -> int:
    try:
        with rdp.Session(cfg.host, cfg.port, user=cfg.user, password=cfg.password, domain=cfg.domain):
            print("Certificate is already trusted (CA-signed or pinned).")
    except rdp.CertificateError as e:
        print(f"Server certificate fingerprint:\n  {e.fingerprint}\n\nIf this is your machine, add to .env:")
        print(f"  RDP_CERT_FINGERPRINT={e.fingerprint}")
    return 0


def _screenshot(cfg: Config) -> int:
    with Runner(cfg).connect() as s:
        s.wait_for_idle(quiet=1.5, timeout=20)
        out = Path(cfg.runs_dir)
        out.mkdir(parents=True, exist_ok=True)
        print("saved", s.save_screenshot(str(out / "screenshot.png")))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="rdp-agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("script", nargs="?", help="path to a .toml script")
    ap.add_argument("--host", help="override RDP_HOST")
    ap.add_argument("--step", action="store_true", help="approve each step before it runs")
    ap.add_argument("--keep-going", action="store_true", help="don't stop at the first failed step")
    ap.add_argument("--check", action="store_true", help="validate the script and exit")
    ap.add_argument("--no-open", action="store_true", help="don't open the HTML report when done")
    ap.add_argument("--screenshot", action="store_true", help="connect, save a screenshot, exit")
    ap.add_argument("--fingerprint", action="store_true", help="print the server certificate fingerprint")
    a = ap.parse_args(argv)

    try:
        script = Script.load(a.script) if a.script else None
    except (StepError, OSError, ValueError) as e:
        print(f"script error: {e}")
        return 2
    if a.check:
        if not script:
            ap.error("--check needs a script")
        print(f"OK: {script.name!r}, {len(script.steps)} steps")
        return 0

    cfg = Config.from_env(host=a.host)
    try:
        if a.fingerprint:
            return _show_fingerprint(cfg)
        if a.screenshot:
            return _screenshot(cfg)
        if not script:
            ap.error("give a script, e.g.  uv run rdp-agent scripts/notepad.toml")
        runner = Runner(cfg, confirm=_confirm if a.step else None)
        result = runner.run(script, stop_on_error=not a.keep_going)
    except rdp.CertificateError as e:
        print("\nThe server's certificate isn't trusted.")
        print(f"Run `uv run rdp-agent --fingerprint`, check it, and pin it. ({e.fingerprint})")
        return 2
    except rdp.AuthenticationError as e:
        print(f"\nLogin rejected: {e}\nCheck RDP_USER / RDP_PASSWORD / RDP_DOMAIN in .env")
        return 2
    except rdp.ConnectionFailed as e:
        print(f"\nCould not connect: {e}")
        return 2
    except KeyboardInterrupt:
        print("\nstopped by user")
        return 130

    print(f"\n{'PASS' if result.success else 'FAIL'}: {result.summary}")
    print(f"report: {result.report}")
    if not a.no_open:
        webbrowser.open(Path(result.report).resolve().as_uri())
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
