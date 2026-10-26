"""Scrub a file held in memory (an email attachment, a zip member, a PDF
embedded file) by its name/type. Returns None when it must not be exported."""

from __future__ import annotations

from pathlib import PurePosixPath

from .base import Context

MAX_DEPTH = 8


def kind_of(name: str, ctype: str = "") -> str | None:
    ext = PurePosixPath(name).suffix.lower()
    if ext == ".eml" or ctype == "message/rfc822":
        return "eml"
    if ext in (".csv", ".tsv") or ctype in ("text/csv", "text/tab-separated-values"):
        return "csv"
    if ext == ".xlsx":
        return "xlsx"
    if ext in (".xml", ".cda", ".ccd", ".ccda") or ctype in ("application/xml", "text/xml"):
        return "xml"
    if ext in (".hl7", ".er7", ".hl7v2") or ctype in ("x-application/hl7-v2+er7", "application/hl7-v2"):
        return "hl7"
    if ext == ".docx":
        return "docx"
    if ext == ".pdf" or ctype == "application/pdf":
        return "pdf"
    if ext == ".json" or ctype == "application/json":
        return "json"
    if ext in (".jsonl", ".ndjson"):
        return "jsonl"
    if ext == ".zip" or ctype in ("application/zip", "application/x-zip-compressed"):
        return "zip"
    if ext in (".txt", ".text", ".md", ".log") or ctype == "text/plain":
        return "text"
    if ext in IMAGE_EXT or (ctype.startswith("image/") and ctype not in ("image/svg+xml",) and not ext):
        return "image"
    if ext in (".dcm", ".dicom") or ctype == "application/dicom":
        return "dicom"
    return None


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jpe", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".webp"}


def scrub_bytes(name: str, data: bytes, ctx: Context, item: str, ctype: str = "", depth: int = 0) -> bytes | None:
    kind = kind_of(name, ctype)
    if kind is None or depth > MAX_DEPTH:
        return None
    if kind == "eml":
        from .email_ import scrub_message_bytes
        return scrub_message_bytes(data, ctx, item)
    if kind == "csv":
        from .tabular import scrub_csv_bytes
        return scrub_csv_bytes(data, ctx, item, tsv=name.lower().endswith(".tsv"))
    if kind == "xlsx":
        from .tabular import scrub_xlsx_bytes
        return scrub_xlsx_bytes(data, ctx, item)
    if kind == "hl7":
        from .hl7 import scrub_hl7_bytes
        return scrub_hl7_bytes(data, ctx, item)
    if kind == "xml":
        from .xml_ import scrub_xml_bytes
        return scrub_xml_bytes(data, ctx, item)
    if kind == "docx":
        from .docx_ import scrub_docx_bytes
        return scrub_docx_bytes(data, ctx, item, depth + 1)
    if kind == "pdf":
        from .pdf import scrub_pdf_bytes
        return scrub_pdf_bytes(data, ctx, item, depth + 1)
    if kind in ("json", "jsonl"):
        from .json_ import scrub_json_bytes
        return scrub_json_bytes(data, ctx, item, jsonl=(kind == "jsonl"))
    if kind == "zip":
        from .archive import scrub_zip_bytes
        return scrub_zip_bytes(data, ctx, item, depth + 1)
    if kind == "image":
        from .image import scrub_image_bytes
        return scrub_image_bytes(data, ctx, item, depth + 1)
    if kind == "dicom":
        from .dicom import scrub_dicom_bytes
        return scrub_dicom_bytes(data, ctx, item, depth + 1)
    if kind == "text":
        from .json_ import decode
        text, enc = decode(data)
        out = ctx.scrubber.scrub(text, ctx.tally).text
        ctx.residual(out, item)
        return out.encode("utf-8" if enc == "utf-8-sig" else enc, errors="replace")
    return None
