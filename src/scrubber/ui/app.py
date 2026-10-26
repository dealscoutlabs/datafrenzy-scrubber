"""Local review screen (FastAPI). Reachable only from this computer.

Shows the identifier counts, a random sample of original-vs-scrubbed text, the
human-review queue (approve/reject), lets reviewers report missed identifiers
(added to the deny list) and asks the scrubber to re-run. Originals are shown
here and nowhere else; nothing on these pages is ever exported.
"""

from __future__ import annotations

import hashlib
import io
import json
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup, escape

from .. import __version__
from ..config import add_deny_terms, deny_additions, load_config, remove_deny_term
from ..identifiers import TOKEN_PREFIX
from ..pipeline import Paths, Runner
from ..state import Store

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}
PREVIEWABLE = {"image", "pdf", "dicom"}
DENY_TYPES = sorted(TOKEN_PREFIX)

_env = Environment(loader=PackageLoader("scrubber", "ui/templates"), autoescape=select_autoescape(["html"]))


def _mark(text: str | None) -> Markup:
    """Escape, then show the ⟦flagged⟧ part highlighted."""
    return Markup(str(escape(text or "")).replace("⟦", "<mark>").replace("⟧", "</mark>"))


_env.filters["mark"] = _mark
_env.filters["timestamp"] = lambda t: time.strftime("%b %d, %Y %I:%M %p", time.localtime(float(t or 0)))


def file_id(rel: str) -> str:
    return hashlib.sha256(rel.encode()).hexdigest()[:16]


class _CurrentPaths:
    """In the desktop app the folders can change while it runs: always use the current ones."""

    def __init__(self, desktop):
        self._desktop = desktop

    def __getattr__(self, name):
        return getattr(self._desktop.paths(), name)


def create_app(paths: Paths | None, config_path: str, desktop=None) -> FastAPI:
    """desktop: hooks from scrubber.desktop.app when running as the Windows/Mac app."""
    app = FastAPI(title="Data Frenzy review", docs_url=None, redoc_url=None, openapi_url=None)
    csrf = secrets.token_urlsafe(32)
    if desktop is not None:
        paths = _CurrentPaths(desktop)

    def runner() -> Runner:
        return Runner(paths, load_config(config_path), workers=1, echo=lambda *_: None)

    def nav_pending() -> int:
        try:
            db = paths.state / "state.db"
            return len(Store(db).reviews("pending")) if db.exists() else 0
        except Exception:
            return 0

    def page(name: str, request: Request, **ctx) -> HTMLResponse:
        lock_state, lock_text = (desktop.lock() if desktop is not None else (None, ""))
        html = _env.get_template(name).render(
            csrf=csrf, version=__version__, reviewer=request.cookies.get("reviewer", ""),
            msg=request.query_params.get("msg", ""), desktop=desktop is not None, path=request.url.path,
            nav_pending=nav_pending(), lock_state=lock_state, lock_text=lock_text,
            mode=desktop.mode() if desktop is not None else "detailed", **ctx)
        return HTMLResponse(html)

    def back(url: str, msg: str, reviewer: str | None = None) -> RedirectResponse:
        resp = RedirectResponse(f"{url}{'&' if '?' in url else '?'}msg={quote(msg)}", status_code=303)
        if reviewer:
            resp.set_cookie("reviewer", reviewer, samesite="strict", httponly=True)
        return resp

    def check_form(token: str, reviewer: str | None = "x") -> None:
        if not secrets.compare_digest(token or "", csrf):
            raise HTTPException(403, "This form expired. Reload the page and try again.")
        if reviewer is not None and not reviewer.strip():
            raise HTTPException(400, "Please enter your name, so the decision can be recorded.")

    def find(r: Runner, fid: str):
        for f in r.store.files():
            if file_id(f["rel"]) == fid:
                return f
        raise HTTPException(404, "No such file")

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = (request.headers.get("host") or "").strip()
        hostname = urlparse(f"//{host}").hostname or ""
        if hostname not in ALLOWED_HOSTS:
            return PlainTextResponse("This page only works at http://localhost", status_code=403)
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer")
            # the Mac app window (WKWebView) sends "Origin: null"; the form's CSRF token still has to match
            if desktop and origin == "null":
                origin = None
            if origin and (urlparse(origin).hostname or "") not in ALLOWED_HOSTS:
                print(f"refused POST {request.url.path} origin={origin!r}", file=sys.stderr, flush=True)
                return PlainTextResponse("Cross-site request refused", status_code=403)
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        # the desktop window (pywebview) runs its own scripts through eval(), e.g. to receive dropped files
        script = "; script-src 'self' 'unsafe-eval'" if desktop else ""
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self' "
                                                   "'unsafe-inline'; form-action 'self'; frame-ancestors 'none'" + script)
        return resp

    # ------------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        r = runner()
        s = r.summary()
        status_path = paths.work / "status.json"
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
        requested = (paths.work / "requests" / "rerun.json").exists()
        running = status.get("state") == "running" and time.time() - status.get("updated", 0) < 600
        if desktop is not None:
            ok, why = desktop.lock()
            batch = desktop.batch()
            from ..desktop.app import batch_files
            rows = [{"name": f["rel"], "status": f["status"], "out_rel": f["out_rel"], "reason": f["reason"] or ""}
                    for f in sorted(batch_files(r.store.files()), key=lambda f: f["rel"].lower())]
            pending_fids = [file_id(rv["rel"]) for rv in r.store.reviews("pending")]
            return page("desktop_home.html", request, s=s, status=status, requested=requested, running=running,
                        refresh=running or requested, lock_ok=ok, lock=why, batch=batch, rows=rows[:300],
                        more_rows=max(0, len(rows) - 300), pending_fids=pending_fids, pending=len(pending_fids))
        return page("index.html", request, s=s, status=status, requested=requested, running=running,
                    refresh=running or requested, app=None, pending=len(r.store.reviews("pending")),
                    now=time.time())

    @app.post("/mode")
    async def set_mode(csrf_token: str = Form(...), mode: str = Form("simple")):
        check_form(csrf_token, None)
        if desktop is not None:
            desktop.set_mode(mode)
        return RedirectResponse("/", status_code=303)

    @app.get("/history", response_class=HTMLResponse)
    async def history(request: Request):
        return page("history.html", request, batches=desktop.history() if desktop is not None else [])

    @app.get("/review", response_class=HTMLResponse)
    async def review_list(request: Request):
        r = runner()
        rows = []
        for rv in r.store.reviews():
            f = r.store.file(rv["rel"])
            if f is None:
                continue
            rows.append({"fid": file_id(rv["rel"]), "rel": rv["rel"], "out_rel": f["out_rel"], "status": rv["status"],
                         "reasons": json.loads(rv["reasons"] or "[]"), "by": rv["decided_by"], "note": rv["note"]})
        rows.sort(key=lambda x: (x["status"] != "pending", x["rel"]))
        return page("review_list.html", request, rows=rows)

    @app.get("/review/{fid}", response_class=HTMLResponse)
    async def review_detail(request: Request, fid: str):
        r = runner()
        f = find(r, fid)
        rv = next((x for x in r.store.reviews() if x["rel"] == f["rel"]), None)
        details = json.loads(rv["details"] or "[]") if rv else []
        return page("review_detail.html", request, f=f, fid=fid, rv=rv, details=details,
                    samples=r.store.samples(f["rel"], limit=10), preview=f["kind"] in PREVIEWABLE,
                    deny_types=DENY_TYPES)

    @app.post("/review/{fid}/{decision}")
    async def decide(fid: str, decision: str, csrf_token: str = Form(...), reviewer: str = Form(""),
                     note: str = Form("")):
        check_form(csrf_token, reviewer)
        if decision not in ("approve", "reject"):
            raise HTTPException(404)
        r = runner()
        f = find(r, fid)
        try:
            (r.approve if decision == "approve" else r.reject)(f["rel"], reviewer.strip(), note.strip())
        except ValueError as exc:
            return back(f"/review/{fid}", str(exc), reviewer)
        r.write_summary()
        word = "approved and exported" if decision == "approve" else "rejected (not exported)"
        return back("/review", f"{f['out_rel']}: {word}", reviewer)

    @app.post("/review-bulk")
    async def decide_many(csrf_token: str = Form(...), reviewer: str = Form(""), decision: str = Form(...),
                          fids: list[str] = Form([]), note: str = Form(""), next: str = Form("/review")):
        check_form(csrf_token, reviewer)
        if decision not in ("approve", "reject"):
            raise HTTPException(404)
        r = runner()
        wanted = set(fids)
        done, failed = 0, 0
        for f in r.store.files():
            if file_id(f["rel"]) not in wanted:
                continue
            try:
                (r.approve if decision == "approve" else r.reject)(f["rel"], reviewer.strip(), note.strip())
                done += 1
            except ValueError:
                failed += 1
        r.write_summary()
        word = "approved and exported" if decision == "approve" else "rejected (not exported)"
        msg = f"{done} file{'s' if done != 1 else ''} {word}" + (f"; {failed} were already decided" if failed else "")
        return back("/" if next == "/" else "/review", msg if done or failed else "No files were selected", reviewer)

    @app.get("/samples", response_class=HTMLResponse)
    async def samples(request: Request):
        r = runner()
        rows = [{"fid": file_id(s["rel"]), "out_rel": (r.store.file(s["rel"]) or {"out_rel": "?"})["out_rel"],
                 "original": s["original"], "scrubbed": s["scrubbed"]} for s in r.store.samples(limit=20, random_order=True)]
        return page("samples.html", request, rows=rows, deny_types=DENY_TYPES)

    @app.post("/misses")
    async def report_miss(csrf_token: str = Form(...), reviewer: str = Form(""), term: str = Form(""),
                          etype: str = Form("PERSON"), note: str = Form(""), fid: str = Form(""),
                          back_to: str = Form("/samples")):
        check_form(csrf_token, reviewer)
        term = term.strip()
        if not term or etype not in TOKEN_PREFIX:
            return back(back_to, "Type the missed text and pick its type.", reviewer)
        r = runner()
        rel = find(r, fid)["rel"] if fid else None
        r.store.add_miss(rel, term, etype, note.strip(), reviewer.strip())
        add_deny_terms(config_path, etype, [term])
        if not back_to.startswith("/"):
            back_to = "/samples"
        return back(back_to, "Added to the deny list. Press Re-run on the home page to apply it.", reviewer)

    @app.get("/deny-list", response_class=HTMLResponse)
    async def deny_list(request: Request):
        return page("deny.html", request, additions=deny_additions(config_path), deny_types=DENY_TYPES)

    @app.post("/deny-list/remove")
    async def deny_remove(csrf_token: str = Form(...), etype: str = Form(...), term: str = Form(...)):
        check_form(csrf_token, None)
        remove_deny_term(config_path, etype, term)
        return back("/deny-list", "Removed. Press Re-run on the home page to apply it.")

    @app.get("/quarantine", response_class=HTMLResponse)
    async def quarantine(request: Request):
        return page("quarantine.html", request, rows=runner().store.quarantined())

    @app.post("/rerun")
    async def rerun(csrf_token: str = Form(...)):
        check_form(csrf_token, None)
        if desktop is not None:
            ok, why = desktop.lock()
            if not ok:
                return back("/", f"Can't start: {why}")
            if not paths.input.is_dir() or not any(paths.input.iterdir()):
                return back("/", "Add files to scrub first.")
        req = paths.work / "requests"
        req.mkdir(parents=True, exist_ok=True)
        (req / "rerun.json").write_text(json.dumps({"requested_at": time.time()}))
        return back("/", "Starting…" if desktop is not None else
                    "Re-run requested. The scrubber picks it up within a few seconds.")

    @app.get("/static/{name}.js")
    async def static_js(name: str):
        if name not in ("desktop", "review"):
            raise HTTPException(404)
        return Response((Path(__file__).with_name("static") / f"{name}.js").read_text(),
                        media_type="text/javascript")

    @app.get("/reports/{name}")
    async def reports(name: str):
        allowed = {"deidentification-report.html": "text/html", "deidentification-report.pdf": "application/pdf",
                   "run-summary.json": "application/json"}
        if name in allowed and (paths.reports / name).exists():
            return FileResponse(paths.reports / name, media_type=allowed[name])
        if name in ("manifest.json", "manifest.csv") and (paths.output / name).exists():
            return FileResponse(paths.output / name)
        raise HTTPException(404)

    @app.get("/preview/{fid}/{side}.png")
    async def preview(fid: str, side: str, page_no: int = 1):
        r = runner()
        f = find(r, fid)
        if f["kind"] not in PREVIEWABLE or side not in ("original", "scrubbed"):
            raise HTTPException(404)
        if side == "original":
            path = Path(f["src"]).resolve()
            roots = [paths.input.resolve(), (paths.work / "pst").resolve()]
            if not any(path.is_relative_to(root) and path != root for root in roots):
                raise HTTPException(404)
        else:
            path = (paths.released if f["status"] == "done" else paths.staging) / f["out_rel"]
        if not path.exists():
            raise HTTPException(404, "Not available")
        return Response(render_preview(path, f["kind"], page_no), media_type="image/png")

    return app


def render_preview(path: Path, kind: str, page_no: int = 1, max_side: int = 1100) -> bytes:
    from PIL import Image, ImageOps
    if kind == "pdf":
        import pymupdf
        with pymupdf.open(path) as doc:
            pg = doc[min(max(page_no, 1), doc.page_count) - 1]
            pix = pg.get_pixmap(dpi=110)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    elif kind == "dicom":
        import pydicom

        from ..formats.dicom import _render
        img = Image.fromarray(_render(pydicom.dcmread(path))[0])
    else:
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def serve(paths: Paths, config_path: str, host: str, port: int) -> int:
    import socket

    import uvicorn
    # Bind the listening socket ourselves: name lookups are blocked in this process.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(64)
    app = create_app(paths, config_path)
    print(f"Review screen ready: open http://localhost:{port} in your browser.")
    uvicorn.Server(uvicorn.Config(app, log_level="warning", proxy_headers=False)).run(sockets=[sock])
    return 0

