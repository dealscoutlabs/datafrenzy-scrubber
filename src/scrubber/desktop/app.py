"""Desktop app: one window, no Docker, no terminal.

Double-click -> a window opens -> choose the folder to scrub and where the
export goes -> Start -> review -> export. Everything runs on this computer.

The window shows the same local review screen as the Docker version, served
on 127.0.0.1 and displayed in a native window (pywebview / Edge WebView2 on
Windows). Scrubbing runs in a separate process so the window stays responsive.

No-internet guarantee without Docker:
  * Windows: the installer adds Windows Firewall rules blocking all outbound
    traffic for the app and its OCR engine; the app checks they are in place
    (and that the firewall is on) and refuses to scrub otherwise.
  * Every process installs the in-process socket guard (scrubber.netguard).
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Data Frenzy Scrubber"
FIREWALL_RULE = "Data Frenzy Scrubber - block internet"


# ------------------------------------------------------------------ locations

def bundle_dir() -> Path:
    """Where bundled files live: the PyInstaller folder, or the repo in development."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[3]


def app_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "DataFrenzyScrubber"
    d.mkdir(parents=True, exist_ok=True)
    return d


def configure_bundle() -> None:
    """Point the scrubber at bundled data, models and the OCR engine (before importing it)."""
    b = bundle_dir()
    os.environ.setdefault("SCRUBBER_DATA_DIR", str(b / "data"))
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    tess = b / "tesseract" / ("tesseract.exe" if os.name == "nt" else "tesseract")
    if tess.exists():
        os.environ.setdefault("TESSDATA_PREFIX", str(tess.parent / "tessdata"))
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = str(tess)
    elif sys.platform == "darwin":
        # Mac trial build: use Homebrew's Tesseract (brew install tesseract)
        import shutil
        found = shutil.which("tesseract") or next(
            (p for p in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract") if Path(p).exists()), None)
        if found:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = found
    if sys.platform == "darwin":
        # never fork a process that has a window open (macOS forbids it after Cocoa starts)
        os.environ.setdefault("SCRUBBER_START_METHOD", "spawn")
    whisper = b / "models" / "whisper"
    if whisper.exists():
        os.environ.setdefault("SCRUBBER_WHISPER_DIR", str(whisper))


# ------------------------------------------------------------------ settings

@dataclass
class Settings:
    input: str = ""
    output: str = ""

    @classmethod
    def load(cls) -> "Settings":
        p = app_dir() / "settings.json"
        try:
            return cls(**json.loads(p.read_text()))
        except (OSError, ValueError, TypeError):
            return cls(output=str(Path.home() / "Documents" / "Data Frenzy Export"))

    def save(self) -> None:
        (app_dir() / "settings.json").write_text(json.dumps(self.__dict__, indent=2))


def config_path() -> Path:
    d = app_dir() / "config"
    d.mkdir(exist_ok=True)
    return d / "scrubber.yaml"


def current_paths():
    from ..pipeline import Paths
    s = Settings.load()
    return Paths(Path(s.input or app_dir() / "no-input-chosen"), Path(s.output or app_dir() / "export"),
                 app_dir() / "work", app_dir() / "state")


# ------------------------------------------------------------------ network lock

def network_lock() -> tuple[bool, str]:
    """(ok, explanation) - is outbound traffic blocked at the operating-system level?"""
    if os.environ.get("SCRUBBER_DEV_ALLOW_NO_LOCK"):
        return True, "Development mode: operating-system lock not checked (the in-app block is on)."
    if sys.platform == "darwin":
        # Trial Mac build: the in-process block is on in every process. The operating-system
        # level block (Apple's app sandbox) needs a signed app from an Apple developer account.
        return True, ("Mac trial build: the app blocks itself from the internet. Apple's system-level "
                      "block comes with the signed Mac app.")
    if os.name != "nt":
        return False, "This app is for Windows and Mac. Use the Docker version on this computer."
    try:
        rule = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE}"],
                              capture_output=True, text=True, creationflags=0x08000000)
        state = subprocess.run(["netsh", "advfirewall", "show", "allprofiles", "state"],
                               capture_output=True, text=True, creationflags=0x08000000)
    except OSError as exc:
        return False, f"Could not check Windows Firewall ({exc})."
    if rule.returncode != 0 or "Block" not in rule.stdout:
        return False, ("The internet block for this app is missing. Reinstall Data Frenzy Scrubber "
                       "(the installer sets it up).")
    import re
    if re.search(r"(?im)^\s*State\s+OFF\b", state.stdout):
        if os.environ.get("SCRUBBER_ALLOW_WITHOUT_FIREWALL"):
            return True, "Windows Firewall is off on some networks; allowed by your IT setting (in-app block on)."
        return False, ("Windows Firewall is turned off on this computer, so the app's internet block "
                       "would not work. Turn Windows Firewall on, then restart the app.")
    return True, "On: Windows Firewall blocks this app from the internet, and the app blocks itself too."


# ------------------------------------------------------------------ scrubbing process

def _scrub(paths, cfg_path: str) -> None:
    from .. import netguard
    netguard.install()
    configure_bundle()
    from ..config import load_config
    from ..pipeline import Runner
    Runner(paths, load_config(cfg_path)).run()


class Worker:
    """Starts a scrub when the screen asks for one (requests/rerun.json), one at a time."""

    def __init__(self):
        self.proc: mp.Process | None = None

    def busy(self) -> bool:
        return self.proc is not None and self.proc.is_alive()

    def loop(self) -> None:
        while True:
            paths = current_paths()
            request = paths.work / "requests" / "rerun.json"
            if request.exists() and not self.busy():
                request.unlink()
                if network_lock()[0] and paths.input.is_dir():
                    self.proc = mp.get_context("spawn").Process(target=_scrub, args=(paths, str(config_path())),
                                                                 daemon=False)
                    self.proc.start()
            time.sleep(1.5)


# ------------------------------------------------------------------ window

class Api:
    """Called from the page (static/desktop.js) through pywebview."""

    def __init__(self):
        self.window = None

    def _pick(self) -> str | None:
        import webview
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else None

    def choose_input(self):
        path = self._pick()
        if path:
            s = Settings.load()
            s.input = path
            s.save()
        return path

    def choose_output(self):
        path = self._pick()
        if path:
            s = Settings.load()
            s.output = path
            s.save()
        return path

    def open_output(self):
        out = Path(Settings.load().output)
        out.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(out)  # noqa: S606 - opens Explorer on the export folder
        elif sys.platform == "darwin":
            subprocess.run(["open", str(out)])
        return True


def serve_in_thread():
    import socket

    import uvicorn

    from ..ui.app import create_app
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))  # any free port, this computer only
    sock.listen(64)
    port = sock.getsockname()[1]
    app = create_app(None, str(config_path()), desktop=DesktopHooks())
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True).start()
    return port


class DesktopHooks:
    """What the review screen needs to know when it runs inside the desktop app."""

    def paths(self):
        return current_paths()

    def settings(self) -> Settings:
        return Settings.load()

    def lock(self) -> tuple[bool, str]:
        return network_lock()


def main(argv: list[str] | None = None) -> int:
    mp.freeze_support()
    argv = sys.argv[1:] if argv is None else argv
    if sys.stdout is None or sys.stderr is None:
        # the packaged app has no console: keep messages in a local log file instead
        logs = app_dir() / "logs"
        logs.mkdir(exist_ok=True)
        stream = open(logs / "app.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    configure_bundle()
    from .. import netguard
    netguard.install()
    if "--self-test" in argv:
        from .selftest import run
        out = argv[argv.index("--self-test-out") + 1] if "--self-test-out" in argv else None
        return run(out)
    import webview
    port = serve_in_thread()
    threading.Thread(target=Worker().loop, daemon=True).start()
    api = Api()
    api.window = webview.create_window(APP_NAME, f"http://127.0.0.1:{port}/", js_api=api, width=1240, height=880,
                                       min_size=(900, 640))
    webview.start(private_mode=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
