"""De-identification audit report (HTML + PDF) for the seller's compliance
officer and for a later Expert Determination review.

The report holds counts, rules, scrubbed file names and decisions only - never
an identifier, never a snippet of original text.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from importlib import metadata
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from .. import __version__
from ..config import DATA_DIR
from ..identifiers import SAFE_HARBOR

HANDLING = {
    1: "Replaced with a consistent token (PERSON_0412, ORG_0031). Names harvested from email headers, name "
       "columns and Slack profiles are matched everywhere.",
    2: "Street addresses and places smaller than a state replaced with tokens; ZIP codes cut to the first three "
       "digits, or 000 for the 17 HHS-listed areas with 20,000 or fewer people.",
    3: "All date elements except the year removed (dates become the year; message times move to January 1). "
       "Ages over 89, and birth dates that reveal them, become 90+.",
    4: "Replaced with a consistent token.", 5: "Replaced with a consistent token.",
    6: "Replaced with a consistent token; email headers get a token address at scrubbed.invalid.",
    7: "Replaced with a consistent token.", 8: "Replaced with a consistent token.",
    9: "Replaced with a consistent token (Medicare MBI, Medicaid, member and policy IDs).",
    10: "Replaced with a consistent token.", 11: "Replaced with a consistent token (incl. NPI, DEA, state licenses).",
    12: "Replaced with a consistent token (VINs, license plates).", 13: "Replaced with a consistent token.",
    14: "Replaced with a token; links point to scrubbed.invalid.", 15: "Replaced with a consistent token.",
    16: "Voice recordings are never exported (scrubbed transcripts only, if enabled); biometric IDs tokenized.",
    17: "Faces detected (OpenCV YuNet) and pixelated in photos, scans, documents and DICOM images.",
    18: "Claim/case/ticket numbers, usernames, social handles and long ID codes replaced with tokens; Slack "
        "IDs and DICOM UIDs replaced with consistent opaque values.",
}


def _version(dist: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "not installed"


def components() -> list[tuple[str, str]]:
    out = [("Data Frenzy Local Scrubber", __version__)]
    for d in ("presidio-analyzer", "presidio-anonymizer", "presidio-image-redactor", "spacy", "en-core-web-lg",
              "PyMuPDF", "python-docx", "openpyxl", "pydicom", "opencv-python", "pillow-heif", "faster-whisper"):
        out.append((d, _version(d)))
    try:
        import pytesseract
        out.append(("Tesseract OCR", str(pytesseract.get_tesseract_version())))
    except Exception:
        out.append(("Tesseract OCR", "not available"))
    yunet = DATA_DIR / "models" / "face_detection_yunet_2023mar.onnx"
    if yunet.exists():
        out.append(("YuNet face model (sha256)", hashlib.sha256(yunet.read_bytes()).hexdigest()[:16] + "…"))
    return out


def context(runner, summary: dict, manifest: dict) -> dict:
    store = runner.store
    flags_by_kind: Counter = Counter()
    risk_rules: Counter = Counter()
    residual_types: Counter = Counter()
    verified = 0
    for f in store.files():
        if f["status"] in ("done", "review", "rejected") and f["kind"] not in ("system",):
            verified += 1
    for r in store.reviews():
        for d in json.loads(r["details"] or "[]"):
            flags_by_kind[d.get("kind", "verification")] += 1
            msg = d.get("message", "")
            if d.get("kind") == "context-risk":
                risk_rules[msg.rsplit("context risk - ", 1)[-1]] += 1
            elif d.get("kind") == "residual":
                residual_types[msg.split("residual ", 1)[-1].split(" ", 1)[0]] += 1
    decisions = [{"file": r["out_rel"] or "(unknown)", "decision": r["decision"], "by": r["decided_by"],
                  "at": dt.datetime.fromtimestamp(r["decided_at"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                  "note": r["note"] or "", "reasons": len(json.loads(r["reasons"] or "[]"))}
                 for r in store.review_log()]
    pending = [{"file": store.file(r["rel"])["out_rel"], "reasons": json.loads(r["reasons"] or "[]")}
               for r in store.reviews("pending")]
    misses = store.misses()
    cfg = runner.cfg
    return {
        "s": summary, "m": manifest, "handling": HANDLING, "categories": SAFE_HARBOR,
        "components": components(), "verified": verified, "flags_by_kind": dict(flags_by_kind),
        "risk_rules": dict(risk_rules), "residual_types": dict(residual_types), "decisions": decisions,
        "pending": pending, "misses": {"count": len(misses), "types": dict(Counter(m["etype"] for m in misses))},
        "deny_list_terms": sum(len(v or []) for v in (cfg.get("deny_list") or {}).values()),
        "allow_orgs": len(cfg["allowlist"]["organizations"]), "thresholds": cfg["thresholds"],
        "residual_threshold": cfg["verification"]["residual_threshold"], "images": cfg["images"],
        "reference_year": cfg["reference_year"], "transcribe": bool(cfg.get("audio", {}).get("transcribe")),
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


_env = Environment(loader=PackageLoader("scrubber", "report/templates"), autoescape=select_autoescape(["html"]))


def render_html(ctx: dict, for_pdf: bool = False) -> str:
    return _env.get_template("audit.html").render(**ctx, for_pdf=for_pdf)


def write_pdf(html: str, path: Path) -> None:
    import pymupdf
    css = (_env.loader.get_source(_env, "audit_pdf.css")[0])
    story = pymupdf.Story(html=html, user_css=css)
    writer = pymupdf.DocumentWriter(str(path))
    page = pymupdf.paper_rect("letter")
    where = page + (54, 54, -54, -54)
    more = True
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()


def write(runner, summary: dict, manifest: dict) -> None:
    reports = runner.paths.reports
    reports.mkdir(parents=True, exist_ok=True)
    ctx = context(runner, summary, manifest)
    (reports / "deidentification-report.html").write_text(render_html(ctx))
    write_pdf(render_html(ctx, for_pdf=True), reports / "deidentification-report.pdf")
