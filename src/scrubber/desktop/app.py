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
    input: str = ""   # unused since batches (kept so older settings files still load)
    output: str = ""
    mode: str = "simple"  # simple: add files, get scrubbed files | detailed: review screens too

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


def batch_dir() -> Path:
    """The batch being worked on: copies of the added files, the scrubbed output and work files.
    "New batch" deletes it; the saved copy the user chose a folder for stays where they put it."""
    return app_dir() / "batch"


def current_paths():
    from ..pipeline import Paths
    b = batch_dir()
    return Paths(b / "input", b / "output", b / "work", app_dir() / "state")


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


# ------------------------------------------------------------------ batches

def _free_name(folder: Path, name: str) -> Path:
    target = folder / name
    stem, suffix, n = Path(name).stem, Path(name).suffix, 2
    while target.exists():
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


# what macOS / Windows make when text (not a file) is dragged out of a window
SNIPPETS = {".textclipping", ".webloc", ".inetloc", ".url", ".shs", ".scf"}
SNIPPET_MSG = ("That was a piece of text, not a file. Drag the file's icon from Finder or File Explorer, "
               "or use Add files.")


def add_to_batch(paths: list[str]) -> int:
    """Copy files and folders into the batch (originals are never touched) and start scrubbing.
    Returns how many items were added."""
    import shutil
    items = [Path(p) for p in paths if p and Path(p).exists() and Path(p).suffix.lower() not in SNIPPETS]
    inbox = current_paths().input
    inbox.mkdir(parents=True, exist_ok=True)
    meta = batch_info()
    if not meta.get("started"):
        meta["started"] = time.time()
        _save_batch_info(meta)
    for item in items:
        target = _free_name(inbox, item.name)
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    if items:
        request_scrub()
    return len(items)


def request_scrub() -> None:
    req = current_paths().work / "requests"
    req.mkdir(parents=True, exist_ok=True)
    (req / "rerun.json").write_text(json.dumps({"requested_at": time.time()}))


def batch_info() -> dict:
    try:
        return json.loads((batch_dir() / "batch.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_batch_info(meta: dict) -> None:
    batch_dir().mkdir(parents=True, exist_ok=True)
    (batch_dir() / "batch.json").write_text(json.dumps(meta, indent=2))


def batch_counts() -> dict:
    from ..state import Store
    db = current_paths().state / "state.db"
    if not db.exists():
        return {"files": 0}
    files = [f for f in Store(db).files() if not f["parent"]]
    by = {}
    for f in files:
        by[f["status"]] = by.get(f["status"], 0) + 1
    return {"files": len(files), "exported": by.get("done", 0), "review": by.get("review", 0),
            "not_exported": sum(by.get(k, 0) for k in ("quarantined", "failed", "rejected")),
            "records": sum(f["records"] or 0 for f in files)}


def history() -> list[dict]:
    try:
        return json.loads((app_dir() / "history.json").read_text())
    except (OSError, ValueError):
        return []


def _record_history(entry: dict) -> None:
    items = [h for h in history() if h.get("id") != entry["id"]] + [entry]
    (app_dir() / "history.json").write_text(json.dumps(items[-500:], indent=2))


def _history_entry() -> dict | None:
    """Counts and where the output was saved - no file contents and no original file names."""
    meta = batch_info()
    if not meta.get("started"):
        return None
    return {"id": str(int(meta["started"])), "started": meta["started"], "updated": time.time(),
            "saved_to": meta.get("saved_to", []), **batch_counts()}


REPORT_FOLDER = "Scrub report"


def save_output(dest: str) -> Path:
    """Copy the batch into a new folder inside dest: the cleaned files at the top, the manifest
    and de-identification report in one "Scrub report" folder next to them."""
    import shutil
    stamp = time.strftime("%Y-%m-%d %H.%M")
    target = _free_name(Path(dest), f"Data Frenzy Scrubbed {stamp}")
    out = current_paths().output
    target.mkdir(parents=True)
    scrubbed = out / "scrubbed"
    if scrubbed.is_dir():
        shutil.copytree(scrubbed, target, dirs_exist_ok=True)
    report = target / REPORT_FOLDER
    if (target / REPORT_FOLDER).exists():
        report = _free_name(target, REPORT_FOLDER)
    report.mkdir()
    for item in out.iterdir():
        if item.name == "scrubbed":
            continue
        (shutil.copytree if item.is_dir() else shutil.copy2)(item, report / item.name)
    meta = batch_info()
    meta.setdefault("saved_to", []).append(str(target))
    _save_batch_info(meta)
    entry = _history_entry()
    if entry:
        _record_history(entry)
    return target


def new_batch() -> None:
    """Remember the batch's counts in History, then delete its copies, output and work files."""
    import shutil
    from ..state import Store
    entry = _history_entry()
    if entry and entry["files"]:
        _record_history(entry)
    db = current_paths().state / "state.db"
    if db.exists():
        store = Store(db)
        store.clear_batch()
        store.close()
    shutil.rmtree(batch_dir(), ignore_errors=True)


def bind_drop(window) -> None:
    """Read the full paths of files dropped on the window (pywebview gives them to Python only)."""
    from webview.dom import DOMEventHandler

    def on_drop(event):
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        print(f"drop: {len(files)} item(s), {len(paths)} path(s)", file=sys.stderr, flush=True)
        if paths and add_to_batch(paths):
            window.evaluate_js("location.reload()")
        elif paths:
            from urllib.parse import quote
            window.evaluate_js(f"location.href = '/?msg={quote(SNIPPET_MSG)}'")

    window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)


# ------------------------------------------------------------------ window

class Api:
    """Called from the page (static/desktop.js) through pywebview."""

    def __init__(self, worker=None):
        self.window = None
        self.worker = worker

    def _folder(self) -> str | None:
        from webview import FileDialog
        result = self.window.create_file_dialog(FileDialog.FOLDER)
        return result[0] if result else None

    def add_files(self):
        from webview import FileDialog
        picked = self.window.create_file_dialog(FileDialog.OPEN, allow_multiple=True)
        return add_to_batch(list(picked or []))

    def add_folder(self):
        folder = self._folder()
        return add_to_batch([folder]) if folder else 0

    def save_output(self):
        dest = self._folder()
        if not dest:
            return None
        target = save_output(dest)
        _reveal(target)
        return str(target)

    def open_output(self):
        out = current_paths().output
        out.mkdir(parents=True, exist_ok=True)
        _reveal(out)
        return True

    def new_batch(self):
        if self.worker is not None and self.worker.busy():
            return None
        new_batch()
        return True


def _reveal(folder: Path) -> None:
    if os.name == "nt":
        os.startfile(folder)  # noqa: S606 - opens Explorer on the folder
    elif sys.platform == "darwin":
        subprocess.run(["open", str(folder)])


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

    def mode(self) -> str:
        return "detailed" if Settings.load().mode == "detailed" else "simple"

    def set_mode(self, mode: str) -> None:
        s = Settings.load()
        s.mode = "detailed" if mode == "detailed" else "simple"
        s.save()

    def batch(self) -> dict:
        return {**batch_info(), **batch_counts()}

    def history(self) -> list[dict]:
        return list(reversed(history()))


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
    worker = Worker()
    threading.Thread(target=worker.loop, daemon=True).start()
    api = Api(worker)
    api.window = webview.create_window(APP_NAME, f"http://127.0.0.1:{port}/", js_api=api, width=1240, height=880,
                                       min_size=(900, 640))
    api.window.events.loaded += lambda: bind_drop(api.window)  # every page load gets the drop handler
    webview.start(private_mode=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
