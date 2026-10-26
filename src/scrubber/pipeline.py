"""Run orchestration: scan -> convert .pst -> harvest names -> scrub in parallel
-> verify -> release passing files to /output (others wait for review).

Resumable and idempotent: every file and shard is tracked in /state/state.db.
Files that are already done with the same content and config are skipped.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .config import config_hash, load_zip3_restricted
from .detect.engine import Detector
from .detect.harvest import harvest_file, known_entries
from .formats import archive, audio, dicom, docx_, email_, image, json_, mbox, pdf, tabular, text, xml_
from .formats.dispatch import IMAGE_EXT
from .formats.base import Context, Quarantined
from .identifiers import SAFE_HARBOR, ENTITY_TO_CATEGORY
from .keys import load_or_create_key
from .state import Store
from .transform.generalize import Generalizer
from .transform.pseudonym import Pseudonymizer
from .transform.scrub import Tally, TextScrubber

log = logging.getLogger("scrubber")

SUPPORTED = {".eml": "eml", ".mbox": "mbox", ".mbx": "mbox", ".pst": "pst", ".ost": "pst", ".csv": "csv",
             ".tsv": "csv", ".xlsx": "xlsx", ".ods": "ods", ".xml": "xml", ".cda": "xml", ".ccd": "xml", ".ccda": "xml", ".txt": "text", ".text": "text", ".md": "text", ".log": "text",
             ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl", ".zip": "zip", ".docx": "docx", ".pdf": "pdf",
             ".dcm": "dicom", ".dicom": "dicom", **{e: "image" for e in IMAGE_EXT}}
HANDLED = {"eml", "mbox", "csv", "xlsx", "ods", "xml", "text", "json", "jsonl", "zip", "docx", "pdf", "image", "dicom", "media"}
MEDIA_REASON = "audio/video contains voiceprints (biometric identifier) - never exported"
NO_WHISPER = ("audio/video contains voiceprints (biometric identifier) - never exported; transcription is "
              "turned on but this install has no Whisper model (rebuild with WITH_WHISPER=1)")
UNSUPPORTED = {
    ".xls": "old Excel format - save as .xlsx and re-run", ".xlsm": "Excel file with macros - save as .xlsx and re-run",
    ".doc": "old Word format - save as .docx and re-run", ".rtf": "RTF is not supported - save as .docx and re-run",
    ".docm": "Word file with macros - save as .docx and re-run",
    ".msg": "Outlook .msg - export the folder to .mbox or .pst instead (see README)",
    ".svg": "vector images (SVG) are not supported - save as PNG and re-run",
    ".psd": "Photoshop files are not supported - save as PNG/JPEG and re-run",
}
ZIP_EXPAND_FILES = 10            # zips with this many files or more are unpacked into separate items
ZIP_EXPAND_BYTES = 50 << 20     # ... or this much data
ZIP_MAX_BYTES = 50 << 30        # never unpack more than this (zip bombs)
SYSTEM_FILES = {".ds_store", "thumbs.db", "desktop.ini", ".localized"}


@dataclass
class Paths:
    input: Path
    output: Path
    work: Path
    state: Path

    @property
    def staging(self) -> Path:
        return self.work / "staging"

    @property
    def parts(self) -> Path:
        return self.work / "parts"

    @property
    def released(self) -> Path:
        return self.output / "scrubbed"

    @property
    def reports(self) -> Path:
        return self.output / "reports"


def classify(path: Path) -> tuple[str, str | None]:
    """-> (kind, quarantine reason or None)"""
    name = path.name.lower()
    ext = path.suffix.lower()
    if name in SYSTEM_FILES or name.startswith("._"):
        return "system", "operating-system file, not exported"
    if ext in SUPPORTED:
        return SUPPORTED[ext], None
    if ext in email_.MEDIA_EXT:
        return "media", MEDIA_REASON
    if ext in UNSUPPORTED:
        return "unsupported", UNSUPPORTED[ext]
    if not ext and mbox.looks_like_mbox(path):
        return "mbox", None
    if dicom.looks_like_dicom(path):
        return "dicom", None
    return "unknown", "unknown, encrypted or binary format - not exported"


# ------------------------------------------------------------------ worker side

_W: dict = {}


def _make_context(store: Store, rel: str, out_rel: str) -> Context:
    return Context(cfg=_W["cfg"], scrubber=_W["scrubber"], store=store, rel=rel, out_rel=out_rel)


def _restore_tally(raw: str | None) -> Tally:
    t = Tally()
    if raw:
        d = json.loads(raw)
        t.counts.update(d.get("counts", {}))
        t.years = set(d.get("years", []))
        t.records = d.get("records", 0)
        t.messages = d.get("messages", 0)
        t.issues = list(d.get("issues", []))
        t.flags = list(d.get("flags", []))
        t.samples = list(d.get("samples", []))
        t.held = list(d.get("held", []))
    return t


def make_scrubber(cfg: dict, store: Store, key: bytes) -> TextScrubber:
    """Detector (with names harvested so far), pseudonymizer and generalizer."""
    allow_orgs = {o.lower() for o in cfg["allowlist"]["organizations"]}
    entries, full_names = known_entries(store.known_entities(), allow_orgs)
    detector = Detector(cfg, entries)
    pseudo = Pseudonymizer(key, store, cfg)
    pseudo.set_person_aliases(full_names)
    gen = Generalizer(int(cfg["reference_year"]), load_zip3_restricted())
    return TextScrubber(detector, pseudo, gen)


def start_method() -> str:
    forced = os.environ.get("SCRUBBER_START_METHOD")
    if forced:
        return forced
    # fork only on Linux: Windows can't, and macOS crashes system libraries in forked children
    return "fork" if sys.platform.startswith("linux") else "spawn"


def init_worker(paths: "Paths", cfg: dict) -> None:
    """Worker set-up when processes are spawned (Windows): rebuild everything here."""
    from . import netguard
    netguard.install()
    store = Store(paths.state / "state.db")
    _W.update(cfg=cfg, store=store, paths=paths, scrubber=make_scrubber(cfg, store, load_or_create_key(paths.state)))


def total_memory_gb() -> float | None:
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (ValueError, OSError, AttributeError):
        return None


def auto_workers() -> int:
    """One worker per core - but when workers each load their own language model
    (Windows), leave room for about 1.5 GB per worker plus 2 GB for everything else."""
    cores = os.cpu_count() or 1
    if start_method() == "fork":
        return cores
    mem = total_memory_gb()
    if mem is None:
        return max(1, min(cores, 2))
    return max(1, min(cores, int((mem - 2) // 1.5)))


def run_job(job: tuple[str, int]) -> tuple[str, int, str]:
    rel, idx = job
    store: Store = _W["store"]
    paths: Paths = _W["paths"]
    f = store.file(rel)
    shard = next(s for s in store.shards(rel) if s["idx"] == idx)
    ctx = _make_context(store, rel, f["out_rel"])
    store.finish_shard(rel, idx, "processing")
    try:
        src = Path(f["src"])
        if f["kind"] == "mbox":
            ctx.tally = _restore_tally(shard["counts"]) if shard["progress"] else Tally()
            part = paths.parts / _file_id(rel) / f"shard-{idx:05d}.mbox"
            mbox.process_shard(src, part, ctx, idx, shard["start"], shard["stop"],
                               (shard["progress"], shard["out_offset"]),
                               lambda n, off: store.checkpoint(rel, idx, n, off, ctx.tally.to_dict()),
                               int(_W["cfg"]["mbox"]["checkpoint_every"]))
        else:
            dst = paths.staging / f["out_rel"]
            if f["kind"] == "eml":
                email_.process_eml(src, dst, ctx)
            elif f["kind"] == "csv":
                ctx.tally = _restore_tally(shard["counts"]) if shard["progress"] else Tally()
                tabular.process_csv(src, dst, ctx, (shard["progress"], shard["out_offset"]),
                                    lambda n, off: store.checkpoint(rel, idx, n, off, ctx.tally.to_dict()))
            elif f["kind"] == "xlsx":
                tabular.process_xlsx(src, dst, ctx)
            elif f["kind"] == "ods":
                tabular.process_ods(src, dst, ctx)
            elif f["kind"] == "xml":
                xml_.process_xml(src, dst, ctx)
            elif f["kind"] == "text":
                text.process_text(src, dst, ctx)
            elif f["kind"] in ("json", "jsonl"):
                limit = int(_W["cfg"]["json"]["max_mb"]) * 1024 * 1024
                if f["kind"] == "json" and src.stat().st_size > limit:
                    raise Quarantined(f"JSON file larger than {limit >> 20} MB - export it as JSON Lines (.jsonl)")
                json_.process_json(src, dst, ctx, jsonl=(f["kind"] == "jsonl"))
            elif f["kind"] == "zip":
                archive.process_zip(src, dst, ctx)
            elif f["kind"] == "docx":
                docx_.process_docx(src, dst, ctx)
            elif f["kind"] == "pdf":
                pdf.process_pdf(src, dst, ctx)
            elif f["kind"] == "image":
                image.process_image(src, dst, ctx)
            elif f["kind"] == "dicom":
                dicom.process_dicom(src, dst, ctx)
            elif f["kind"] == "media":
                audio.process_media(src, dst, ctx)
            else:
                raise Quarantined(f"no handler for {f['kind']}")
        store.finish_shard(rel, idx, "done", counts=ctx.tally.to_dict(), error=None)
        return rel, idx, "done"
    except Quarantined as q:
        store.finish_shard(rel, idx, "failed", error=f"QUARANTINE:{q.reason}")
        return rel, idx, "failed"
    except Exception as exc:
        _log_error(paths, rel, exc)
        store.finish_shard(rel, idx, "failed", error=type(exc).__name__)
        return rel, idx, "failed"


def _log_error(paths: Paths, rel: str, exc: Exception) -> None:
    # Local log only (the work folder never leaves this machine).
    logs = paths.work / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "errors.log", "a") as fh:
        fh.write(f"--- {dt.datetime.now().isoformat()} {rel}\n{traceback.format_exc()}\n")


def _file_id(rel: str) -> str:
    import hashlib
    return hashlib.sha256(rel.encode()).hexdigest()[:16]


# ------------------------------------------------------------------ parent side

class Runner:
    def __init__(self, paths: Paths, cfg: dict, workers: int | None = None, echo=print):
        self.paths = paths
        self.cfg = cfg
        self.echo = echo
        self.chash = config_hash(cfg)
        self.store = Store(paths.state / "state.db")
        self.key = load_or_create_key(paths.state)
        w = workers or cfg.get("workers", "auto")
        self.workers = auto_workers() if w == "auto" else max(1, int(w))
        self.scrubber: TextScrubber | None = None

    # -- steps
    def scan(self) -> None:
        self.store.reset_interrupted()
        seen = set()
        n = 0
        for root, dirs, files in os.walk(self.paths.input):
            dirs.sort()
            for name in sorted(files):
                p = Path(root) / name
                rel = p.relative_to(self.paths.input).as_posix()
                st = p.stat()
                kind, reason = classify(p)
                if kind == "media" and self.cfg["audio"].get("transcribe"):
                    reason = None if audio.transcription_enabled(self.cfg) else NO_WHISPER
                status = self.store.upsert_file(rel, str(p), kind, st.st_size, f"{st.st_size}:{st.st_mtime_ns}",
                                                self.chash)
                if reason and status == "pending":
                    self.store.update_file(rel, status="quarantined", reason=reason)
                seen.add(rel)
                n += 1
        # derived files (from .pst) are kept while their parent exists
        for row in self.store.files():
            if row["rel"] not in seen and not (row["parent"] and row["parent"] in seen):
                self.store.update_file(row["rel"], status="removed")
        self.echo(f"Found {n:,} files in the input folder.")

    def convert_pst(self) -> None:
        for row in self.store.files("pending"):
            if row["kind"] != "pst":
                continue
            out = self.paths.work / "pst" / _file_id(row["rel"])
            if out.exists():
                shutil.rmtree(out)
            out.mkdir(parents=True)
            self.echo(f"Converting Outlook file {row['rel']} to mbox ...")
            if shutil.which("readpst"):
                ok = subprocess.run(["readpst", "-r", "-q", "-o", str(out), row["src"]],
                                    capture_output=True, text=True).returncode == 0
            else:  # Windows app: no readpst there; read the .pst with libpff instead
                from .formats import pst_pff
                try:
                    ok = pst_pff.available() and bool(pst_pff.convert(Path(row["src"]), out))
                except Exception:
                    ok = False
            if not ok:
                self.store.update_file(row["rel"], status="quarantined",
                                       reason="Outlook file could not be read (encrypted or damaged); "
                                              "see README for converting it to mbox in Outlook")
                continue
            for p in sorted(out.rglob("*")):
                if not p.is_file():
                    continue
                inner = p.relative_to(out)
                if p.name == "mbox":
                    derived = f"{row['rel']}.mbox/{inner.parent.as_posix()}.mbox"
                    kind = "mbox"
                else:
                    derived = f"{row['rel']}.mbox/{inner.as_posix()}"
                    kind, _ = classify(p)
                st = p.stat()
                self.store.upsert_file(derived, str(p), kind, st.st_size, f"{st.st_size}:{st.st_mtime_ns}",
                                       self.chash, parent=row["rel"])
            self.store.update_file(row["rel"], status="converted", reason="converted to mbox; see derived files")

    def convert_archives(self) -> None:
        """Large plain zips (not Slack exports) are unpacked into the work folder so each file
        inside is its own work item: scrubbed in parallel, with progress, resumable. Their files
        come out in a folder named "<zip name> (from zip)". Small zips stay one item."""
        import zipfile
        from pathlib import PurePosixPath
        for row in self.store.files("pending"):
            if row["kind"] != "zip":
                continue
            try:
                with zipfile.ZipFile(row["src"]) as z:
                    members = archive.list_members(z)
                    if archive.slack_root([m.filename for m in members]) is not None:
                        continue
                    total = sum(m.file_size for m in members)
                    if len(members) < ZIP_EXPAND_FILES and total < ZIP_EXPAND_BYTES:
                        continue
                    if total > ZIP_MAX_BYTES or any(m.flag_bits & 0x1 for m in members):
                        continue  # huge or password-protected: the in-memory path reports it
                    out = self.paths.work / "zip" / _file_id(row["rel"])
                    if out.exists():
                        shutil.rmtree(out)
                    out.mkdir(parents=True)
                    self.echo(f"Unpacking {row['rel']} ({len(members):,} files) ...")
                    base = (row["rel"][:-4] if row["rel"].lower().endswith(".zip") else row["rel"]) + " (from zip)"
                    for m in members:
                        parts = [p for p in PurePosixPath(m.filename).parts if p not in ("", ".", "..", "/")]
                        if not parts:
                            continue
                        target = out.joinpath(*parts)
                        if out.resolve() not in target.resolve().parents:
                            continue  # never write outside the work folder
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(m) as fin, open(target, "wb") as fout:
                            shutil.copyfileobj(fin, fout, 1 << 20)
                        kind, reason = classify(target)
                        st = target.stat()
                        derived = f"{base}/{'/'.join(parts)}"
                        status = self.store.upsert_file(derived, str(target), kind, st.st_size,
                                                        f"{m.CRC}:{m.file_size}", self.chash, parent=row["rel"])
                        if reason and status == "pending":
                            self.store.update_file(derived, status="quarantined", reason=reason)
            except (zipfile.BadZipFile, OSError):
                continue  # the in-memory path quarantines it with the reason
            self.store.update_file(row["rel"], status="converted", reason="unpacked; see the files inside")

    def harvest(self) -> None:
        todo = [r for r in self.store.files("pending")
                if r["kind"] in ("eml", "mbox", "csv", "xlsx", "ods", "zip", "json", "xml") and not r["harvested"]]
        if todo:
            self.echo(f"Collecting names from email headers and name columns ({len(todo):,} files) ...")
        for row in todo:
            try:
                rows = harvest_file(row["kind"], Path(row["src"]))
            except Exception:
                rows = []
            self.store.add_known(rows)
            self.store.update_file(row["rel"], harvested=1)

    def build_scrubber(self) -> None:
        self.scrubber = make_scrubber(self.cfg, self.store, self.key)

    def assign_output_names(self) -> None:
        rows = self.store.files()
        taken = {r["out_rel"] for r in rows if r["out_rel"] and r["status"] not in ("pending", "quarantined")}
        for r in rows:
            if r["status"] not in ("pending", "quarantined"):
                continue
            parts = [self.scrubber.scrub_name(p) for p in r["rel"].split("/")]
            out = "/".join(parts)
            if r["kind"] == "pst":
                out += ".mbox"
            if r["kind"] == "ods":
                out = out[: -len(".ods")] + ".xlsx" if out.lower().endswith(".ods") else out + ".xlsx"
            if r["kind"] == "media":
                out += audio.TRANSCRIPT_SUFFIX  # only the scrubbed transcript is ever exported
            base, n = out, 2
            while out in taken and out != r["out_rel"]:
                stem, dot, ext = base.rpartition(".")
                out = f"{stem}-{n}.{ext}" if dot and "/" not in ext else f"{base}-{n}"
                n += 1
            taken.add(out)
            self.store.update_file(r["rel"], out_rel=out)
            if r["status"] == "quarantined":
                self.store.quarantine(out, r["rel"], r["reason"], r["kind"], r["size"])

    def plan(self) -> list[tuple[str, int]]:
        jobs = []
        shard_bytes = int(self.cfg["mbox"]["shard_mb"]) * 1024 * 1024
        for r in self.store.files("pending"):
            if r["kind"] not in HANDLED:
                continue
            released = self.paths.released / r["out_rel"]
            if released.exists():
                released.unlink()  # stale output from an earlier config; rebuilt below
            if r["kind"] == "mbox":
                bounds = mbox.shard_bounds(Path(r["src"]), shard_bytes)
            else:
                bounds = [(0, r["size"] or 0)]
            self.store.ensure_shards(r["rel"], bounds)
            self.store.update_file(r["rel"], status="processing")
            jobs += [(r["rel"], s["idx"]) for s in self.store.shards(r["rel"]) if s["status"] != "done"]
        return jobs

    def execute(self, jobs: list[tuple[str, int]]) -> None:
        _W.update(cfg=self.cfg, scrubber=self.scrubber, store=self.store, paths=self.paths)
        total = len(jobs)
        if not total:
            return
        self.echo(f"Scrubbing {total:,} work items on {min(self.workers, total)} CPU core(s) ...")
        done = 0
        t0 = time.time()
        if self.workers <= 1 or total == 1:
            results = map(run_job, jobs)
            for rel, _idx, _status in results:
                done += 1
                self._progress(done, total, t0)
                self._finalize_if_ready(rel)
            return
        method = start_method()
        ctx = mp.get_context(method)
        if method == "fork":
            # Linux/macOS: children share the loaded language model copy-on-write
            pool = ctx.Pool(processes=min(self.workers, total))
        else:
            # Windows: each worker starts fresh and loads its own copy of the model
            pool = ctx.Pool(processes=min(self.workers, total), initializer=init_worker,
                            initargs=(self.paths, self.cfg))
        with pool:
            for rel, _idx, _status in pool.imap_unordered(run_job, jobs, chunksize=1):
                done += 1
                self._progress(done, total, t0)
                self._finalize_if_ready(rel)

    def _progress(self, done: int, total: int, t0: float) -> None:
        self.status(phase="scrubbing", done=done, total=total)
        if done == total or done % max(1, total // 20) == 0:
            self.echo(f"  {done:,}/{total:,} done ({time.time() - t0:,.0f}s)")

    def status(self, **fields) -> None:
        """Progress for the review screen (/work/status.json)."""
        path = self.paths.work / "status.json"
        try:
            current = json.loads(path.read_text()) if path.exists() else {}
        except ValueError:
            current = {}
        current.update(fields, updated=time.time())
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current))
        tmp.replace(path)

    def _finalize_if_ready(self, rel: str) -> None:
        shards = self.store.shards(rel)
        if any(s["status"] not in ("done", "failed") for s in shards):
            return
        f = self.store.file(rel)
        tally = Tally()
        for s in shards:
            tally.add(_restore_tally(s["counts"]))
        failed = [s for s in shards if s["status"] == "failed"]
        staged = self.paths.staging / f["out_rel"]
        if failed:
            err = failed[0]["error"] or "error"
            reason = err.split(":", 1)[1] if err.startswith("QUARANTINE:") else (
                f"could not be opened or processed ({err}) - possibly encrypted or damaged")
            self.store.update_file(rel, status="quarantined", reason=reason)
            self.store.quarantine(f["out_rel"], rel, reason, f["kind"], f["size"])
            if staged.exists():
                staged.unlink()
            self._cleanup_parts(rel)
            return
        if f["kind"] == "mbox":
            parts = [self.paths.parts / _file_id(rel) / f"shard-{s['idx']:05d}.mbox" for s in shards]
            mbox.assemble(parts, staged)
            ctx = Context(cfg=self.cfg, scrubber=self.scrubber, store=self.store, rel=rel, out_rel=f["out_rel"])
            mbox.verify_mbox(staged, tally.messages, ctx)
            tally.issues += ctx.tally.issues
            self._register_held(f, tally.held)
            self._cleanup_parts(rel)
        fields = dict(counts=dict(tally.counts), years=sorted(tally.years), records=tally.records,
                      messages=tally.messages, verify=tally.issues)
        self.store.set_samples(rel, tally.samples)
        if tally.issues:
            self.store.update_file(rel, status="review", reason="needs human review", **fields)
            self.store.add_review(rel, tally.issues, tally.flags)
        else:
            self._release(rel, f["out_rel"])
            self.store.update_file(rel, status="done", reason=None, **fields)

    def _register_held(self, f, held: list[dict]) -> None:
        """Each message held from a mailbox becomes its own review item. Approving it
        appends it to the exported mailbox; rejecting it keeps it out for good."""
        for h in held:
            rel = f"{f['rel']}::message {h['name']}"
            out_rel = f"{f['out_rel']}.held/message-{h['name']}.eml"
            staged = self.paths.staging / out_rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(h["path"], staged)
            size = staged.stat().st_size
            self.store.upsert_file(rel, f["src"], "held-message", size, f"held:{size}", self.chash, parent=f["rel"])
            self.store.update_file(rel, status="review", reason="needs human review", out_rel=out_rel, records=1,
                                   messages=1, verify=h["issues"])
            self.store.add_review(rel, h["issues"], h["flags"])

    def _release(self, rel: str, out_rel: str) -> None:
        src = self.paths.staging / out_rel
        dst = self.paths.released / out_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))

    def _cleanup_parts(self, rel: str) -> None:
        d = self.paths.parts / _file_id(rel)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    def cleanup_conversions(self) -> None:
        """Converted .pst mailboxes hold original data: delete once their files are finished."""
        for row in self.store.files("converted"):
            derived = [r for r in self.store.files() if r["parent"] == row["rel"]]
            if derived and all(r["status"] in ("done", "review", "quarantined", "rejected") for r in derived):
                if all(r["status"] != "review" for r in derived):
                    for kind in ("pst", "zip"):
                        shutil.rmtree(self.paths.work / kind / _file_id(row["rel"]), ignore_errors=True)

    # -- review decisions (the Phase 4 UI calls these too)
    def approve(self, rel: str, by: str, note: str = "") -> None:
        f = self.store.file(rel)
        if f is None or f["status"] != "review":
            raise ValueError(f"{rel} is not waiting for review")
        if f["kind"] == "held-message":
            self._append_to_mailbox(f)
        else:
            self._release(rel, f["out_rel"])
        self.store.decide_review(rel, "approved", by, note)
        self.store.update_file(rel, status="done", reason="approved after human review")

    def _append_to_mailbox(self, f) -> None:
        parent = self.store.file(f["parent"])
        base = self.paths.released if parent["status"] == "done" else self.paths.staging
        box = base / parent["out_rel"]
        staged = self.paths.staging / f["out_rel"]
        data = staged.read_bytes()
        body = mbox.escape_from(data)
        with open(box, "ab") as out:
            out.write(mbox.from_line(data) + body + (b"" if body.endswith(b"\n") else b"\n") + b"\n")
        staged.unlink()
        try:
            staged.parent.rmdir()
        except OSError:
            pass

    def reject(self, rel: str, by: str, note: str = "") -> None:
        f = self.store.file(rel)
        if f is None or f["status"] != "review":
            raise ValueError(f"{rel} is not waiting for review")
        staged = self.paths.staging / f["out_rel"]
        if staged.exists():
            staged.unlink()
        self.store.decide_review(rel, "rejected", by, note)
        self.store.update_file(rel, status="rejected", reason="rejected in human review - not exported")

    # -- summary
    def summary(self) -> dict:
        files = self.store.files()
        by_status: dict[str, int] = {}
        counts: dict[str, int] = {}
        years: set[int] = set()
        records = 0
        for f in files:
            by_status[f["status"]] = by_status.get(f["status"], 0) + 1
            if f["status"] == "done" and f["counts"]:
                for k, v in json.loads(f["counts"]).items():
                    counts[k] = counts.get(k, 0) + v
                years |= set(json.loads(f["years"] or "[]"))
                records += f["records"] or 0
        per_category = []
        for cat in SAFE_HARBOR:
            per_category.append({"number": cat.number, "identifier": cat.title,
                                 "removed": sum(counts.get(e, 0) for e in cat.entities),
                                 "by_type": {e: counts.get(e, 0) for e in cat.entities}})
        quarantined = [{"item": q["item"], "reason": q["reason"]} for q in self.store.quarantined()]
        media = sum(1 for f in files if f["kind"] == "media")
        per_category[15]["removed"] += media
        per_category[15]["by_type"]["MEDIA_BIOMETRIC"] = media
        review = [{"file": self.store.file(r["rel"])["out_rel"], "status": r["status"],
                   "reasons": json.loads(r["reasons"] or "[]"), "decided_by": r["decided_by"]}
                  for r in self.store.reviews()]
        return {
            "tool": "Data Frenzy Local Scrubber", "version": __version__,
            "method": "HIPAA Safe Harbor, 45 CFR 164.514(b)(2)",
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "config_hash": self.chash, "files": by_status, "records_exported": records,
            "date_range_years": [min(years), max(years)] if years else None,
            "identifiers": per_category, "quarantined": quarantined, "review": review,
            "unmapped_counts": {k: v for k, v in counts.items() if k not in ENTITY_TO_CATEGORY},
        }

    def write_summary(self) -> dict:
        """run-summary.json, the data manifest and the de-identification audit report."""
        from .report import audit, manifest
        s = self.summary()
        self.paths.reports.mkdir(parents=True, exist_ok=True)
        (self.paths.reports / "run-summary.json").write_text(json.dumps(s, indent=2))
        m = manifest.build(self)
        manifest.write(m, self.paths.output)
        audit.write(self, s, m)
        return s

    def run(self) -> dict:
        for p in (self.paths.output, self.paths.work, self.paths.state):
            p.mkdir(parents=True, exist_ok=True)
        self.status(state="running", phase="scanning", done=0, total=0, started=time.time())
        self.scan()
        self.convert_pst()
        self.convert_archives()
        self.harvest()
        self.echo("Loading the language model (offline) ...")
        self.build_scrubber()
        self.assign_output_names()
        jobs = self.plan()
        self.execute(jobs)
        # anything whose shards were all done in an earlier interrupted run
        for r in self.store.files("processing"):
            self._finalize_if_ready(r["rel"])
        self.cleanup_conversions()
        self.status(phase="writing reports")
        s = self.write_summary()
        self.status(state="idle", phase="finished", finished=time.time(), files=s["files"])
        self.echo(f"Finished. Exported: {s['files'].get('done', 0):,} files. "
                  f"Waiting for review: {s['files'].get('review', 0):,}. "
                  f"Not exported (quarantined): {s['files'].get('quarantined', 0):,}.")
        return s
