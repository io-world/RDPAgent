"""Live view: streams the remote screen as MJPEG over HTTP while a script runs.

Open http://127.0.0.1:<port>/ in a browser (or /stream, plain MJPEG, in VLC / OpenCV). A background thread
sleeps until the screen changes, grabs at most `fps` frames a second and JPEG-encodes them, so an
unchanged screen costs nothing. It reads from the agent's own session: Windows allows one RDP
session per user, so connecting a second client to watch would log the agent out.
"""

from __future__ import annotations

import io
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pythonwrapfreerdp as rdp

# Fits the whole screen in the window by default; click to switch to actual size (scrolls to where you
# clicked) and back. The choice is remembered per browser. Frames come from /frame.jpg long-polling, not
# from an <img src="/stream"> MJPEG tag: browsers' MJPEG-in-<img> support is unreliable (headless Chrome
# shows a broken image), while fetching plain JPEGs works everywhere.
PAGE = b"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>RDP Agent live</title>
<style>
html,body{margin:0;height:100%;background:#111}
body{display:flex;align-items:center;justify-content:center}
img{display:block;max-width:100vw;max-height:100vh;cursor:zoom-in}
body.actual{display:block;overflow:auto}
body.actual img{max-width:none;max-height:none;cursor:zoom-out}
#hint{position:fixed;top:8px;right:8px;font:12px system-ui,sans-serif;color:#ddd;background:#000b;
  padding:3px 8px;border-radius:4px;pointer-events:none}
</style>
<img id=screen alt="remote screen"><div id=hint></div>
<script>
const body = document.body, img = document.getElementById("screen"), hint = document.getElementById("hint");
const actual = () => body.classList.contains("actual");
let status = "";
const showHint = () => hint.textContent =
  status || (actual() ? "actual size \\u00b7 click to fit" : "fit to window \\u00b7 click for actual size");
try { if (localStorage.getItem("rdp-live-view") === "actual") body.classList.add("actual"); } catch (e) {}
img.onclick = (e) => {
  const r = img.getBoundingClientRect(), fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
  body.classList.toggle("actual");
  try { localStorage.setItem("rdp-live-view", actual() ? "actual" : "fit"); } catch (e) {}
  if (actual()) window.scrollTo(fx * img.offsetWidth - innerWidth / 2, fy * img.offsetHeight - innerHeight / 2);
  showHint();
};
showHint();
(async () => {  // each request waits on the server until there is a newer frame than `n`
  let n = 0, shown = null;
  for (;;) {
    try {
      const r = await fetch(`/frame.jpg?after=${n}`, {cache: "no-store"});
      if (r.status === 200) {
        n = +r.headers.get("X-Frame");
        const url = URL.createObjectURL(await r.blob());
        img.src = url;
        if (shown) URL.revokeObjectURL(shown);
        shown = url;
      } else if (r.status !== 204) throw new Error(r.status);
      if (status) { status = ""; showHint(); }
    } catch (e) {
      status = "disconnected (run finished?) \\u00b7 retrying"; showHint();
      await new Promise((ok) => setTimeout(ok, 2000));
    }
  }
})();
</script>"""


def _p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20)[18] if len(values) > 1 else (values[0] if values else 0.0)


def _peak_per_second(times: list[float], weights: list[float]) -> float:
    """Largest sum of `weights` falling inside any 1-second window."""
    best, lo, total = 0.0, 0, 0.0
    for hi, t in enumerate(times):
        total += weights[hi]
        while times[lo] <= t - 1.0:
            total -= weights[lo]
            lo += 1
        best = max(best, total)
    return best


class LiveStream:
    poll_timeout = 25.0  # seconds a /frame.jpg request waits for a new frame before answering 204

    def __init__(self, session, port: int = 8765, fps: float = 10.0, quality: int = 80, scale: float = 1.0,
                 host: str = "127.0.0.1"):  # fmt: skip
        self.session, self.port, self.host = session, port, host
        self.fps, self.quality, self.scale = fps, quality, scale
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._jpeg, self._frame_no = b"", 0  # latest encoded frame
        self._size: tuple[int, int] | None = None  # remote screen size, from the frames
        # stats
        self._changes = 0
        self._grab_ms: list[float] = []
        self._encode_ms: list[float] = []
        self._ready_ms: list[float] = []  # screen change noticed -> JPEG ready (includes the fps cap)
        self._sizes: list[int] = []
        self._times: list[float] = []
        self._thread_cpu = 0.0
        self._clients = 0
        self._sent_frames = 0
        self._sent_bytes = 0
        self._started = self._ended = 0.0

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def __enter__(self) -> LiveStream:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        self._started = time.monotonic()
        self._server = ThreadingHTTPServer((self.host, self.port), self._handler())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]  # port 0 = pick a free one
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self._producer = threading.Thread(target=self._produce, daemon=True)
        self._producer.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self._producer.join(3)
        self._server.shutdown()
        self._server.server_close()
        self._ended = time.monotonic()

    # ------------------------------------------------------------ producer
    def _produce(self) -> None:
        cpu0 = time.thread_time()
        seq, last, interval = 0, 0.0, 1.0 / self.fps
        try:
            while not self._stop.is_set():
                if not self.session.wait_for_change(timeout=0.5, after=seq):
                    continue
                noticed = time.monotonic()
                if (wait := last + interval - noticed) > 0:
                    self._stop.wait(wait)  # fps cap: changes in the meantime are folded into this frame
                t0 = time.monotonic()
                frame = self.session.frame()
                t1 = time.monotonic()
                self._size = (frame.width, frame.height)
                img = frame.to_pil()
                if self.scale != 1.0:
                    img = img.resize((round(frame.width * self.scale), round(frame.height * self.scale)))
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=self.quality)
                data = buf.getvalue()
                t2 = last = time.monotonic()
                self._changes += frame.seq - seq
                seq = frame.seq
                self._grab_ms.append((t1 - t0) * 1000)
                self._encode_ms.append((t2 - t1) * 1000)
                self._ready_ms.append((t2 - noticed) * 1000)
                self._sizes.append(len(data))
                self._times.append(t2)
                with self._cond:
                    self._jpeg, self._frame_no = data, self._frame_no + 1
                    self._cond.notify_all()
        except rdp.RDPError:
            pass  # session closed under us: the run is over
        finally:
            self._thread_cpu = time.thread_time() - cpu0

    # ------------------------------------------------------------ http
    def _handler(self):
        live = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the agent's console clean
                pass

            def do_GET(self):
                if self.path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(PAGE)
                elif self.path == "/stream":
                    self._stream()
                elif self.path.startswith("/frame.jpg"):
                    self._frame()
                else:
                    self.send_error(404)

            def _frame(self):
                """Long-poll: answer with the newest frame once it is newer than ?after=N (204 if none came)."""
                query = parse_qs(urlsplit(self.path).query)
                after = int(query.get("after", ["0"])[0] or 0)
                deadline = time.monotonic() + live.poll_timeout
                with live._cond:
                    if after > live._frame_no:  # page left open from an earlier run
                        after = 0
                    while live._frame_no <= after and not live._stop.is_set():
                        if (remaining := deadline - time.monotonic()) <= 0:
                            break
                        live._cond.wait(remaining)
                    data, latest = live._jpeg, live._frame_no
                if after == 0:
                    live._clients += 1
                if latest <= after or not data:
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame", str(latest))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    return
                live._sent_frames += 1
                live._sent_bytes += len(data)

            def _stream(self):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                live._clients += 1
                seen = 0
                try:
                    # Each part ends with the next boundary, written straight after the JPEG: browsers only
                    # show a part once its closing boundary arrives, so a still screen would never appear.
                    self.wfile.write(b"--frame\r\n")
                    while not live._stop.is_set():
                        with live._cond:
                            if live._frame_no == seen:
                                live._cond.wait(timeout=1)
                            data, latest = live._jpeg, live._frame_no
                        if latest == seen or not data:
                            continue
                        seen = latest
                        head = f"Content-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n\r\n".encode()
                        self.wfile.write(head + data + b"\r\n--frame\r\n")
                        live._sent_frames += 1
                        live._sent_bytes += len(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # viewer closed the tab

        return Handler

    # ------------------------------------------------------------ stats
    def summary(self) -> dict:
        seconds = (self._ended or time.monotonic()) - self._started
        w, h = self._size or (0, 0)
        n = len(self._sizes)
        total = sum(self._sizes)
        r = lambda v, d=1: round(v, d)  # noqa: E731
        ms = lambda xs: {"avg": r(statistics.fmean(xs) if xs else 0), "p95": r(_p95(xs))}  # noqa: E731
        return {
            "screen_size": f"{w}x{h}",
            "stream_size": f"{round(w * self.scale)}x{round(h * self.scale)}",
            "fps_cap": self.fps,
            "jpeg_quality": self.quality,
            "seconds": r(seconds),
            "screen_changes": self._changes,
            "frames_encoded": n,
            "avg_fps": r(n / seconds if seconds else 0, 2),
            "peak_fps": _peak_per_second(self._times, [1] * n),
            "grab_ms": ms(self._grab_ms),
            "encode_ms": ms(self._encode_ms),
            "change_to_ready_ms": ms(self._ready_ms),
            "frame_kb": {"avg": r(total / n / 1024 if n else 0), "max": r(max(self._sizes, default=0) / 1024)},
            "encoded_mb": r(total / 1e6, 2),
            "avg_mbit_s": r(total * 8 / 1e6 / seconds if seconds else 0, 2),
            "peak_mbit_s": r(_peak_per_second(self._times, self._sizes) * 8 / 1e6, 2),
            "stream_cpu_seconds": r(self._thread_cpu, 2),
            "stream_cpu_pct_of_one_core": r(self._thread_cpu / seconds * 100 if seconds else 0),
            "viewers": self._clients,
            "frames_sent": self._sent_frames,
            "sent_mb": r(self._sent_bytes / 1e6, 2),
        }
