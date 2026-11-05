"""Yoink - paste a link, get the video.

Links can also come from the Yoink Android app: share a link to Yoink on the
phone and it arrives here over the home wifi (see LinkServer).

A small tkinter front end over yt-dlp. Links go into a queue that is worked
off one at a time, always at the best quality, merged into an .mp4 by ffmpeg.

yt-dlp.exe and ffmpeg.exe live in a bin folder next to the program instead of
being baked into it, so yt-dlp can update itself without a rebuild.
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import secrets
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import qrcode  # only used to draw the pairing QR code
    HAS_QR = True
except ImportError:
    HAS_QR = False

APP_NAME = "Yoink"
FROZEN = getattr(sys, "frozen", False)
IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0

YTDLP_DOWNLOAD_URL = (
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
)
DEFAULT_VIDEOCUT = (
    Path.home() / "Desktop" / "Projects" / "VideoCut" / "dist"
    / "VideoCutPlayer" / "VideoCutPlayer.exe"
)
DEFAULT_BROWSER = "firefox"
POLL_MS = 100

# Phone link: the Yoink Android app POSTs shared links here, over the home
# wifi. Every request needs the token from the pairing QR code.
DEFAULT_PORT = 47824
TOKEN_HEADER = "X-Yoink-Token"
MAX_BODY = 64 * 1024

URL_RE = re.compile(r"https?://[^\s<>\"']+")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Marker prefixes Yoink asks yt-dlp to print, so its output can be parsed
# without guessing at yt-dlp's normal log format.
TAG_PROGRESS = "[yoink-progress]"
TAG_TITLE = "[yoink-title]"
TAG_FORMAT = "[yoink-format]"
TAG_FILE = "[yoink-file]"


# ------------------------------------------------------------------ paths

def app_dir():
    """Folder holding the exe (frozen) or this script (from source)."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bin_dir():
    return app_dir() / "bin"


def ytdlp_path():
    local = bin_dir() / f"yt-dlp{EXE}"
    if local.exists():
        return local
    found = shutil.which("yt-dlp")
    return Path(found) if found else None


def ffmpeg_dir():
    """Folder with ffmpeg in it, or None when yt-dlp has to find it on PATH."""
    if (bin_dir() / f"ffmpeg{EXE}").exists():
        return bin_dir()
    return None


def has_ffmpeg():
    return ffmpeg_dir() is not None or shutil.which("ffmpeg") is not None


def config_path():
    if FROZEN:
        folder = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = Path.home()
        return folder / "yoink_config.json"
    return app_dir() / "yoink_config.json"


def load_config():
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except OSError:
        pass


def extract_urls(text):
    """Every http(s) link in a blob of text, in order, without duplicates."""
    seen = []
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(".,);]")
        if url not in seen:
            seen.append(url)
    return seen


def kill_tree(proc):
    """Stop yt-dlp and the ffmpeg it may have started."""
    if proc is None or proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
    else:
        proc.kill()


# ------------------------------------------------------------------ phone link

def lan_addresses():
    """IPv4 addresses the phone might reach this PC on, best guess first."""
    ips = []
    try:
        # No packet is sent: connecting a UDP socket only picks the interface
        # that owns the default route, which is the one the wifi is on.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("127.", "169.254.")) or ip in ips:
                continue
            ips.append(ip)
    except OSError:
        pass
    return ips


def pc_name():
    return os.environ.get("COMPUTERNAME") or socket.gethostname() or "PC"


def pairing_uri(hosts, port, token):
    from urllib.parse import quote
    return (f"yoink://pair?v=1&h={','.join(hosts)}&p={port}"
            f"&t={quote(token, safe='')}&n={quote(pc_name(), safe='')}")


class LinkServer:
    """Tiny HTTP server for the phone app. Runs in its own thread.

    GET  /api/info   -> {"app": "yoink", "pc": name}
    POST /api/links  {"urls": [...]} -> {"accepted": n}
    """

    def __init__(self, port, get_token, on_links):
        self.port = port
        self.get_token = get_token
        self.on_links = on_links
        self.httpd = None

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "Yoink"

            def log_message(self, fmt, *args):  # keep the console quiet
                pass

            def _send(self, code, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                given = self.headers.get(TOKEN_HEADER, "")
                if given and secrets.compare_digest(given, server.get_token()):
                    return True
                self._send(401, {"error": "unknown phone, scan the QR code again"})
                return False

            def do_GET(self):
                if self.path != "/api/info":
                    self._send(404, {"error": "not found"})
                    return
                if self._authorized():
                    self._send(200, {"app": "yoink", "pc": pc_name()})

            def do_POST(self):
                if self.path != "/api/links":
                    self._send(404, {"error": "not found"})
                    return
                if not self._authorized():
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if length < 0 or length > MAX_BODY:
                    self._send(413, {"error": "too big"})
                    return
                try:
                    data = json.loads(self.rfile.read(length) or b"{}")
                    raw = data.get("urls", [])
                    if not isinstance(raw, list):
                        raise ValueError
                except ValueError:
                    self._send(400, {"error": "bad json"})
                    return
                urls = []
                for item in raw:
                    for url in extract_urls(str(item)):
                        if url not in urls:
                            urls.append(url)
                if urls:
                    server.on_links(urls)
                self._send(200, {"accepted": len(urls)})

        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


# ------------------------------------------------------------------ jobs

class Job:
    WAITING = "Waiting"
    RUNNING = "Downloading"
    PROCESSING = "Merging"
    DONE = "Done"
    FAILED = "Failed"
    STOPPED = "Stopped"

    _next_id = 0

    def __init__(self, url, folder):
        Job._next_id += 1
        self.id = f"job{Job._next_id}"
        self.url = url
        self.folder = folder
        self.status = Job.WAITING
        self.title = ""
        self.percent = 0.0
        self.speed = ""
        self.eta = ""
        self.parts = 1
        self.part = 1
        self.filepath = None
        self.error = ""
        self.proc = None
        self.stop_requested = False

    def label(self):
        return self.title or self.url

    def progress_text(self):
        if self.status == Job.RUNNING:
            text = f"{self.percent:5.1f}%"
            if self.parts > 1:
                text += f"  ({self.part}/{self.parts})"
            if self.speed:
                text += f"  {self.speed}"
            if self.eta:
                text += f"  ETA {self.eta}"
            return text
        if self.status == Job.DONE:
            return "100%"
        return ""


def build_command(job, ytdlp, use_cookies, browser):
    out_template = str(Path(job.folder) / "%(title).120B [%(id)s].%(ext)s")
    cmd = [
        str(ytdlp),
        "--newline",
        "--no-playlist",
        "--no-mtime",
        "--encoding", "utf-8",
        # best video + best audio, or the best single file if that is all
        # there is; merged or remuxed into an mp4 either way
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "--remux-video", "mp4",
        "-o", out_template,
        # --print makes yt-dlp quiet, so ask for progress explicitly
        "--no-simulate",
        "--progress",
        "--progress-template",
        f"download:{TAG_PROGRESS} %(progress._percent_str)s"
        f"|%(progress._speed_str)s|%(progress._eta_str)s",
        "--print", f"before_dl:{TAG_TITLE} %(title)s",
        "--print", f"before_dl:{TAG_FORMAT} %(format_id)s",
        "--print", f"after_move:{TAG_FILE} %(filepath)s",
    ]
    ff = ffmpeg_dir()
    if ff is not None:
        cmd += ["--ffmpeg-location", str(ff)]
    if use_cookies:
        cmd += ["--cookies-from-browser", browser]
    cmd.append(job.url)
    return cmd


def parse_percent(text):
    try:
        return float(text.strip().rstrip("%"))
    except ValueError:
        return None


def clean_speed(text):
    text = text.strip()
    return "" if text in ("", "N/A", "Unknown", "Unknown B/s") else text


def clean_eta(text):
    text = text.strip()
    return "" if text in ("", "N/A", "Unknown", "NA") else text


def friendly_error(lines, used_cookies):
    """Turn yt-dlp's last error line into something readable."""
    errors = [l for l in lines if l.startswith("ERROR:")]
    raw = errors[-1] if errors else (lines[-1] if lines else "Unknown error")
    raw = raw.replace("ERROR:", "", 1).strip()
    low = raw.lower()
    hint = ""
    if used_cookies and ("cookie" in low or "firefox" in low):
        hint = ("  -- Could not read Firefox cookies. Is Firefox installed and "
                "logged in? Or untick the cookies box and try again.")
    elif "unsupported url" in low:
        hint = "  -- yt-dlp does not know this site."
    elif "drm" in low:
        hint = "  -- This video is DRM protected. Cannot be downloaded."
    elif "login" in low or "sign in" in low or "authentication" in low:
        hint = "  -- Needs a login. Log in on that site in Firefox, keep cookies on."
    elif "ffmpeg" in low:
        hint = "  -- ffmpeg is missing. Put ffmpeg.exe in the bin folder."
    elif "http error 403" in low or "unable to extract" in low:
        hint = "  -- The site may have changed. Try 'Update yt-dlp'."
    return raw + hint


# ------------------------------------------------------------------ app

class YoinkApp:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.jobs = {}
        self.order = []
        self.active = None
        self.events = queue.Queue()
        self.session_folder = None
        self.last_autofill = ""
        self.busy_tool = False  # yt-dlp update or download running
        self.asking_folder = False
        self.remote_pending = []  # links from the phone waiting for a folder
        self.pair_window = None

        if not self.cfg.get("pair_token"):
            self.cfg["pair_token"] = secrets.token_urlsafe(24)
            save_config(self.cfg)
        self.server = LinkServer(
            int(self.cfg.get("port", DEFAULT_PORT)),
            get_token=lambda: self.cfg["pair_token"],
            on_links=lambda urls: self.events.put(("remote_links", urls)),
        )
        self.phone_var = tk.StringVar(value="")

        root.title(APP_NAME)
        root.geometry("860x520")
        root.minsize(640, 380)

        self.url_var = tk.StringVar()
        self.folder_var = tk.StringVar(value="Not chosen yet (asked on first download)")
        self.cookies_var = tk.BooleanVar(value=self.cfg.get("use_cookies", True))
        self.status_var = tk.StringVar(value="")
        self.version_var = tk.StringVar(value="yt-dlp: checking...")

        self._build_ui()

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<FocusIn>", self._on_focus)
        root.after(POLL_MS, self._poll)
        root.after(150, self._startup_checks)
        self._start_server()

    # ---------------------------------------------------------- layout

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        link_box = ttk.LabelFrame(outer, text="Link", padding=8)
        link_box.pack(fill="x")
        link_box.columnconfigure(0, weight=1)

        self.entry = ttk.Entry(link_box, textvariable=self.url_var)
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.entry.bind("<Return>", lambda e: self._add_from_entry())
        ttk.Button(link_box, text="Paste", width=8,
                   command=self._paste).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(link_box, text="Yoink!", width=10,
                   command=self._add_from_entry).grid(row=0, column=2)
        ttk.Label(link_box, foreground="#555",
                  text="Paste one or more links. Enter adds them to the queue."
                  ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        opts = ttk.Frame(outer, padding=(0, 8, 0, 4))
        opts.pack(fill="x")
        opts.columnconfigure(1, weight=1)
        ttk.Label(opts, text="Saving to:").grid(row=0, column=0, sticky="w")
        ttk.Label(opts, textvariable=self.folder_var, foreground="#333"
                  ).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(opts, text="Change...", command=self._change_folder
                   ).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(opts, text="Open folder", command=self._open_folder
                   ).grid(row=0, column=3)

        ttk.Checkbutton(
            opts, variable=self.cookies_var, command=self._cookies_toggled,
            text="Use Firefox cookies (needed for X and logged-in stuff)",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(opts, textvariable=self.version_var, foreground="#555"
                  ).grid(row=1, column=2, sticky="e", padx=(0, 6), pady=(6, 0))
        self.update_btn = ttk.Button(opts, text="Update yt-dlp",
                                     command=self._update_ytdlp)
        self.update_btn.grid(row=1, column=3, pady=(6, 0))

        ttk.Label(opts, textvariable=self.phone_var, foreground="#555"
                  ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(opts, text="Pair phone...", command=self._show_pairing
                   ).grid(row=2, column=3, pady=(6, 0))

        list_box = ttk.LabelFrame(outer, text="Queue", padding=8)
        list_box.pack(fill="both", expand=True)
        list_box.columnconfigure(0, weight=1)
        list_box.rowconfigure(0, weight=1)

        cols = ("status", "progress", "title")
        self.tree = ttk.Treeview(list_box, columns=cols, show="headings",
                                 selectmode="extended")
        self.tree.heading("status", text="Status")
        self.tree.heading("progress", text="Progress")
        self.tree.heading("title", text="Video")
        self.tree.column("status", width=95, stretch=False)
        self.tree.column("progress", width=230, stretch=False)
        self.tree.column("title", width=420)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_box, orient="vertical",
                               command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_selected_info())
        self.tree.bind("<Double-1>", lambda e: self._open_in_videocut())
        self.tree.bind("<Delete>", lambda e: self._remove_selected())

        self.tree.tag_configure("done", foreground="#15803d")
        self.tree.tag_configure("failed", foreground="#b91c1c")
        self.tree.tag_configure("running", foreground="#1d4ed8")
        self.tree.tag_configure("stopped", foreground="#92400e")

        btns = ttk.Frame(list_box, padding=(0, 8, 0, 0))
        btns.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Button(btns, text="Open in VideoCut",
                   command=self._open_in_videocut).pack(side="left")
        ttk.Button(btns, text="Show file",
                   command=self._show_file).pack(side="left", padx=6)
        ttk.Button(btns, text="Clear finished",
                   command=self._clear_finished).pack(side="right")
        ttk.Button(btns, text="Remove",
                   command=self._remove_selected).pack(side="right", padx=6)
        ttk.Button(btns, text="Stop",
                   command=self._stop_selected).pack(side="right")
        ttk.Button(btns, text="Retry",
                   command=self._retry_selected).pack(side="right", padx=6)

        status = ttk.Label(outer, textvariable=self.status_var, foreground="#555",
                           wraplength=820, justify="left")
        status.pack(fill="x", pady=(6, 0))

        self.entry.focus_set()

    # ---------------------------------------------------------- startup

    def _startup_checks(self):
        self._autofill_from_clipboard()
        if ytdlp_path() is None:
            if messagebox.askyesno(
                APP_NAME,
                "yt-dlp.exe is not in the bin folder yet.\n\n"
                "Download the latest version from GitHub now?",
            ):
                self._download_ytdlp()
            else:
                self.version_var.set("yt-dlp: missing")
                self._say("Yoink cannot download anything without yt-dlp.exe "
                          f"in {bin_dir()}")
        else:
            self._check_version()
        if not has_ffmpeg():
            self._say("ffmpeg not found. Put ffmpeg.exe in the bin folder, "
                      "otherwise best quality video and audio cannot be merged.")

    def _check_version(self):
        exe = ytdlp_path()
        if exe is None:
            self.version_var.set("yt-dlp: missing")
            return

        def work():
            try:
                out = subprocess.run(
                    [str(exe), "--version"], capture_output=True, text=True,
                    timeout=30, creationflags=NO_WINDOW,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                out = ""
            self.events.put(("version", out or "unknown"))

        threading.Thread(target=work, daemon=True).start()

    def _download_ytdlp(self):
        if self.busy_tool:
            return
        self.busy_tool = True
        self.version_var.set("yt-dlp: downloading...")

        def work():
            target = bin_dir() / "yt-dlp.exe"
            part = target.with_suffix(".exe.part")
            try:
                bin_dir().mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(YTDLP_DOWNLOAD_URL, part)
                os.replace(part, target)
                self.events.put(("tool_done", "yt-dlp downloaded."))
            except Exception as exc:  # noqa: BLE001 - shown to the user
                try:
                    part.unlink()
                except OSError:
                    pass
                self.events.put(("tool_done",
                                 f"Could not download yt-dlp: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _update_ytdlp(self):
        exe = ytdlp_path()
        if exe is None:
            self._download_ytdlp()
            return
        if self.active is not None:
            messagebox.showinfo(APP_NAME, "Wait until the current download "
                                          "is finished, then update.")
            return
        if self.busy_tool:
            return
        self.busy_tool = True
        self.update_btn.configure(state="disabled")
        self.version_var.set("yt-dlp: updating...")

        def work():
            try:
                res = subprocess.run(
                    [str(exe), "-U"], capture_output=True, text=True,
                    timeout=300, creationflags=NO_WINDOW,
                )
                text = (res.stdout + res.stderr).strip()
                lines = [l for l in text.splitlines() if l.strip()]
                msg = lines[-1] if lines else "Update finished."
            except (OSError, subprocess.SubprocessError) as exc:
                msg = f"Update failed: {exc}"
            self.events.put(("tool_done", msg))

        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------- clipboard

    def _clipboard_text(self):
        try:
            return self.root.clipboard_get()
        except tk.TclError:
            return ""

    def _autofill_from_clipboard(self):
        if self.url_var.get().strip():
            return
        text = self._clipboard_text()
        if text == self.last_autofill:
            return
        urls = [u for u in extract_urls(text) if not self._already_queued(u)]
        if not urls:
            return
        self.last_autofill = text
        self.url_var.set(" ".join(urls))
        self.entry.select_range(0, "end")
        self.entry.icursor("end")

    def _on_focus(self, event):
        if event.widget is self.root:
            self._autofill_from_clipboard()

    def _paste(self):
        text = self._clipboard_text()
        urls = extract_urls(text)
        if urls:
            self.url_var.set(" ".join(urls))
        else:
            self._say("No link on the clipboard.")
        self.entry.focus_set()

    # ---------------------------------------------------------- folder

    def _ask_folder(self):
        start = self.cfg.get("last_folder")
        if not start or not Path(start).is_dir():
            start = str(Path.home() / "Downloads")
        folder = filedialog.askdirectory(
            parent=self.root, initialdir=start, mustexist=True,
            title="Where should Yoink save videos this session?",
        )
        if not folder:
            return None
        folder = str(Path(folder))
        self.session_folder = folder
        self.folder_var.set(folder)
        self.cfg["last_folder"] = folder
        save_config(self.cfg)
        return folder

    def _change_folder(self):
        if self._ask_folder():
            self._flush_remote()

    def _open_folder(self):
        folder = self.session_folder or self.cfg.get("last_folder")
        if folder and Path(folder).is_dir():
            self._startfile(folder)
        else:
            self._say("No folder chosen yet.")

    def _cookies_toggled(self):
        self.cfg["use_cookies"] = bool(self.cookies_var.get())
        save_config(self.cfg)

    # ---------------------------------------------------------- queue

    def _already_queued(self, url):
        return any(j.url == url and j.status in (Job.WAITING, Job.RUNNING,
                                                 Job.PROCESSING)
                   for j in self.jobs.values())

    def _add_from_entry(self):
        urls = extract_urls(self.url_var.get())
        if not urls:
            self._say("That does not look like a link.")
            return
        if self._add_urls(urls, "") is not None:
            self.url_var.set("")
        self._flush_remote()

    def _flush_remote(self):
        """Queue links that came in from the phone, once there is a folder."""
        if not self.remote_pending or self.asking_folder:
            return
        urls = list(self.remote_pending)
        if self._add_urls(urls, " from your phone") is not None:
            self.remote_pending = [u for u in self.remote_pending if u not in urls]
        else:
            self._say(f"{len(self.remote_pending)} link(s) from your phone are "
                      "waiting. Choose a folder (Change...) to start them.")

    def _add_urls(self, urls, source):
        """Queue urls. Returns how many were added, or None without a folder."""
        if self.asking_folder:
            return None
        folder = self.session_folder
        if not folder:
            self.asking_folder = True
            try:
                folder = self._ask_folder()
            finally:
                self.asking_folder = False
        if not folder:
            self._say("No folder chosen, nothing added.")
            return None
        added = 0
        for url in urls:
            if self._already_queued(url):
                continue
            job = Job(url, folder)
            self.jobs[job.id] = job
            self.order.append(job.id)
            self.tree.insert("", "end", iid=job.id)
            self._refresh(job)
            added += 1
        skipped = len(urls) - added
        msg = f"Added {added} link{'s' if added != 1 else ''}{source} to the queue."
        if skipped:
            msg += f" Skipped {skipped} already in the queue."
        self._say(msg)
        self._kick()
        return added

    # ---------------------------------------------------------- phone

    def _start_server(self):
        try:
            self.server.start()
        except OSError as exc:
            self.phone_var.set(f"Phone link off: port {self.server.port} is "
                               f"busy ({exc.strerror or exc}). Is Yoink open twice?")
            return
        ips = lan_addresses()
        where = ips[0] if ips else "this PC"
        self.phone_var.set(f"Phone link on: {where}:{self.server.port}. "
                           "Share a link to Yoink on your phone.")

    def _show_pairing(self):
        if self.pair_window is not None and self.pair_window.winfo_exists():
            self.pair_window.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("Pair phone")
        win.resizable(False, False)
        self.pair_window = win
        frame = ttk.Frame(win, padding=14)
        frame.pack()
        ttk.Label(frame, justify="center", text=(
            "Open Yoink on your phone and tap 'QR-code scannen'.\n"
            "Phone and PC must be on the same wifi.")).pack()
        canvas = tk.Canvas(frame, width=300, height=300, bg="white",
                           highlightthickness=0)
        canvas.pack(pady=10)
        info = ttk.Label(frame, foreground="#555", justify="center")
        info.pack()
        ttk.Button(frame, text="Unpair all phones",
                   command=lambda: self._unpair(canvas, info)).pack(pady=(10, 0))
        self._draw_qr(canvas, info)

    def _draw_qr(self, canvas, info):
        canvas.delete("all")
        ips = lan_addresses()
        uri = pairing_uri(ips, self.server.port, self.cfg["pair_token"])
        if not ips:
            info.configure(text="No network address found. Is this PC on the wifi?")
        else:
            info.configure(text="Addresses: " + ", ".join(ips))
        if not HAS_QR:
            canvas.create_text(150, 150, width=270, text=(
                "The qrcode package is missing.\n\npip install qrcode\n\n"
                "then restart Yoink. (build.bat installs it for the exe.)"))
            return
        qr = qrcode.QRCode(border=2,
                           error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(uri)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        cell = max(1, 300 // len(matrix))
        offset = (300 - cell * len(matrix)) // 2
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    x0, y0 = offset + x * cell, offset + y * cell
                    canvas.create_rectangle(x0, y0, x0 + cell, y0 + cell,
                                            fill="black", width=0)

    def _unpair(self, canvas, info):
        if not messagebox.askyesno(
            APP_NAME, "Make a new secret key? Every phone has to scan the "
                      "QR code again.", parent=self.pair_window):
            return
        self.cfg["pair_token"] = secrets.token_urlsafe(24)
        save_config(self.cfg)
        self._draw_qr(canvas, info)

    def _kick(self):
        """Start the next waiting job if nothing is running."""
        if self.active is not None:
            return
        exe = ytdlp_path()
        for job_id in self.order:
            job = self.jobs.get(job_id)
            if job and job.status == Job.WAITING:
                if exe is None:
                    self._say("yt-dlp.exe is missing. Click 'Update yt-dlp' "
                              "to download it.")
                    return
                self._start(job, exe)
                return

    def _start(self, job, exe):
        self.active = job.id
        job.status = Job.RUNNING
        job.percent = 0.0
        job.part = 1
        job.parts = 1
        job.error = ""
        job.stop_requested = False
        self._refresh(job)
        use_cookies = bool(self.cookies_var.get())
        browser = self.cfg.get("browser", DEFAULT_BROWSER)
        cmd = build_command(job, exe, use_cookies, browser)
        threading.Thread(target=self._run_job, args=(job, cmd, use_cookies),
                         daemon=True).start()

    def _run_job(self, job, cmd, used_cookies):
        """Worker thread: runs yt-dlp and reports back through self.events."""
        tail = []
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=NO_WINDOW,
            )
        except OSError as exc:
            self.events.put(("finished", job.id, -1, f"Could not start yt-dlp: {exc}"))
            return
        job.proc = proc
        last_pct = 0.0
        for raw in proc.stdout:
            line = ANSI_RE.sub("", raw).strip()
            if not line:
                continue
            if line.startswith(TAG_PROGRESS):
                pct_s, _, rest = line[len(TAG_PROGRESS):].partition("|")
                speed_s, _, eta_s = rest.partition("|")
                pct = parse_percent(pct_s)
                if pct is None:
                    continue
                if pct + 20 < last_pct:
                    # percentage jumped back: the next part (audio) started
                    self.events.put(("next_part", job.id))
                last_pct = pct
                self.events.put(("progress", job.id, pct,
                                 clean_speed(speed_s), clean_eta(eta_s)))
            elif line.startswith(TAG_TITLE):
                self.events.put(("title", job.id, line[len(TAG_TITLE):].strip()))
            elif line.startswith(TAG_FORMAT):
                fmt = line[len(TAG_FORMAT):].strip()
                self.events.put(("parts", job.id, fmt.count("+") + 1))
            elif line.startswith(TAG_FILE):
                self.events.put(("file", job.id, line[len(TAG_FILE):].strip()))
            else:
                tail.append(line)
                tail = tail[-30:]
        rc = proc.wait()
        err = "" if rc == 0 else friendly_error(tail, used_cookies)
        self.events.put(("finished", job.id, rc, err))

    # ---------------------------------------------------------- events

    def _poll(self):
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "version":
            self.version_var.set(f"yt-dlp {ev[1]}")
            return
        if kind == "remote_links":
            for url in ev[1]:
                if url not in self.remote_pending:
                    self.remote_pending.append(url)
            self._flush_remote()
            return
        if kind == "tool_done":
            self.busy_tool = False
            self.update_btn.configure(state="normal")
            self._say(ev[1])
            self._check_version()
            self._kick()
            return

        job = self.jobs.get(ev[1])
        if job is None:
            return
        if kind == "progress":
            _, _, pct, speed, eta = ev
            job.percent, job.speed, job.eta = pct, speed, eta
            if pct >= 100.0 and job.part >= job.parts:
                job.status = Job.PROCESSING
            elif job.status == Job.PROCESSING and pct < 100.0:
                job.status = Job.RUNNING
        elif kind == "next_part":
            job.part += 1
            job.parts = max(job.parts, job.part)
            job.status = Job.RUNNING
        elif kind == "parts":
            job.parts = ev[2]
        elif kind == "title":
            job.title = ev[2]
        elif kind == "file":
            job.filepath = ev[2]
        elif kind == "finished":
            _, _, rc, err = ev
            job.proc = None
            if job.stop_requested:
                job.status = Job.STOPPED
                job.error = ""
            elif rc == 0:
                job.status = Job.DONE
                job.percent = 100.0
            else:
                job.status = Job.FAILED
                job.error = err
            if self.active == job.id:
                self.active = None
            self._refresh(job)
            self._show_selected_info()
            if job.status == Job.DONE:
                self._say(f"Done: {job.label()}")
            elif job.status == Job.FAILED:
                self._say(f"Failed: {job.label()}\n{job.error}")
            self._kick()
            return
        self._refresh(job)

    def _refresh(self, job):
        if not self.tree.exists(job.id):
            return
        tag = {
            Job.DONE: "done", Job.FAILED: "failed", Job.STOPPED: "stopped",
            Job.RUNNING: "running", Job.PROCESSING: "running",
        }.get(job.status, "")
        self.tree.item(job.id, values=(job.status, job.progress_text(),
                                       job.label()), tags=(tag,) if tag else ())

    # ---------------------------------------------------------- buttons

    def _selected_jobs(self):
        return [self.jobs[i] for i in self.tree.selection() if i in self.jobs]

    def _first_done_selected(self):
        for job in self._selected_jobs():
            if job.status == Job.DONE and job.filepath and Path(job.filepath).exists():
                return job
        return None

    def _show_selected_info(self):
        jobs = self._selected_jobs()
        if len(jobs) != 1:
            return
        job = jobs[0]
        if job.status == Job.FAILED and job.error:
            self._say(f"{job.url}\n{job.error}")
        elif job.filepath:
            self._say(job.filepath)
        else:
            self._say(job.url)

    def _videocut_exe(self):
        path = Path(self.cfg.get("videocut", str(DEFAULT_VIDEOCUT)))
        if path.exists():
            return path
        messagebox.showinfo(
            APP_NAME, "Cannot find VideoCutPlayer.exe. Point Yoink to it once "
                      "and it will remember.")
        picked = filedialog.askopenfilename(
            parent=self.root, title="Find VideoCutPlayer.exe",
            filetypes=[("Programs", "*.exe"), ("All files", "*.*")],
        )
        if not picked:
            return None
        self.cfg["videocut"] = str(Path(picked))
        save_config(self.cfg)
        return Path(picked)

    def _open_in_videocut(self):
        job = self._first_done_selected()
        if job is None:
            self._say("Select a finished download first.")
            return
        exe = self._videocut_exe()
        if exe is None:
            return
        try:
            subprocess.Popen([str(exe), job.filepath], cwd=str(exe.parent))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not start VideoCut:\n{exc}")

    def _show_file(self):
        job = self._first_done_selected()
        if job is None:
            self._say("Select a finished download first.")
            return
        if IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(job.filepath)])
        else:
            self._startfile(str(Path(job.filepath).parent))

    def _retry_selected(self):
        count = 0
        for job in self._selected_jobs():
            if job.status in (Job.FAILED, Job.STOPPED):
                job.status = Job.WAITING
                job.error = ""
                job.percent = 0.0
                self._refresh(job)
                count += 1
        if count:
            self._kick()
        else:
            self._say("Select a failed or stopped download to retry.")

    def _stop_selected(self):
        for job in self._selected_jobs():
            if job.status == Job.WAITING:
                job.status = Job.STOPPED
                self._refresh(job)
            elif job.status in (Job.RUNNING, Job.PROCESSING):
                job.stop_requested = True
                kill_tree(job.proc)

    def _remove_selected(self):
        for job in self._selected_jobs():
            if job.status in (Job.RUNNING, Job.PROCESSING):
                continue  # stop it first
            self.tree.delete(job.id)
            self.order.remove(job.id)
            del self.jobs[job.id]

    def _clear_finished(self):
        for job_id in list(self.order):
            if self.jobs[job_id].status == Job.DONE:
                self.tree.delete(job_id)
                self.order.remove(job_id)
                del self.jobs[job_id]

    # ---------------------------------------------------------- misc

    def _say(self, text):
        self.status_var.set(text)

    @staticmethod
    def _startfile(path):
        if IS_WINDOWS:
            os.startfile(path)  # noqa: S606 - opens Explorer
        else:
            subprocess.Popen(["xdg-open", path])

    def _on_close(self):
        if self.active is not None:
            if not messagebox.askyesno(
                APP_NAME, "A download is still running. Stop it and quit?"):
                return
            job = self.jobs.get(self.active)
            if job:
                job.stop_requested = True
                kill_tree(job.proc)
        self.server.stop()
        self.root.destroy()


def main():
    if IS_WINDOWS:
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001 - older Windows, ignore
            pass
    root = tk.Tk()
    YoinkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
