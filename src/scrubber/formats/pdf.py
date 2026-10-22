"""PDFs with a text layer (PyMuPDF).

Identifiers are found on each page's extracted text, then removed with TRUE
redaction (add_redact_annot + apply_redactions deletes the characters from the
content stream), and the pseudonym token is drawn where the text was. The
file is then rewritten without its old revisions. Metadata, XMP, annotations,
form fields, links, bookmarks and embedded files are scrubbed too. Scanned
pages (mostly image) are left for Phase 3 and the file is not exported.
"""

from __future__ import annotations

from pathlib import Path

from .base import Context, Quarantined

SCANNED_IMAGE_SHARE = 0.30   # a page this covered by images is treated as scanned
UNREADABLE_SHARE = 0.05      # share of garbage characters that means the text layer can't be trusted
FONT = "helv"


def _pymupdf():
    import pymupdf
    return pymupdf


def _page_text(words) -> tuple[str, list[tuple[int, int]]]:
    """Join PyMuPDF words into text; returns text and each word's (start, end)."""
    parts, offsets, pos = [], [], 0
    prev = None
    for w in words:
        key = (w[5], w[6])  # block, line
        if prev is not None:
            sep = " " if key == prev else ("\n" if key[0] == prev[0] else "\n\n")
            parts.append(sep)
            pos += len(sep)
        offsets.append((pos, pos + len(w[4])))
        parts.append(w[4])
        pos += len(w[4])
        prev = key
    return "".join(parts), offsets


def _garbage_share(text: str) -> float:
    if not text:
        return 0.0
    bad = sum(1 for c in text if c == "�" or 0xE000 <= ord(c) <= 0xF8FF or (ord(c) < 32 and c not in "\n\t"))
    return bad / len(text)


def _image_share(page) -> float:
    area = abs(page.rect)
    if not area:
        return 0.0
    covered = 0.0
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            covered += abs(r & page.rect)
    return min(1.0, covered / area)


def _fit(pymupdf, text: str, width: float, height: float) -> float:
    fs = max(4.0, min(height * 0.8, 12.0))
    while fs > 4.0 and pymupdf.get_text_length(text, fontname=FONT, fontsize=fs) > width:
        fs -= 0.5
    return fs


def scrub_page(page, ctx: Context, item: str) -> tuple[list[str], str]:
    """Redact one page. Returns (removed strings, page text before redaction)."""
    pymupdf = _pymupdf()
    words = page.get_text("words", sort=False)
    text, offsets = _page_text(words)
    if _garbage_share(text) > UNREADABLE_SHARE:
        ctx.issue(f"{item}: text layer is unreadable (unusual font encoding) - check this page by eye")
    spans = ctx.scrubber.plan_many([text], ctx.tally)[0] if text.strip() else []
    removed, overlays = [], []
    for start, end, repl in spans:
        hit = [i for i, (a, b) in enumerate(offsets) if a < end and start < b]
        if not hit:
            continue
        removed.append(text[start:end])
        lines: dict = {}
        for i in hit:
            w = words[i]
            r = pymupdf.Rect(w[:4])
            key = (w[5], w[6])
            lines[key] = lines[key] | r if key in lines else r
        for n, rect in enumerate(lines.values()):
            inset = pymupdf.Rect(rect.x0 + 0.3, rect.y0 + 0.2, rect.x1 - 0.3, rect.y1 - 0.2)
            page.add_redact_annot(inset, fill=(1, 1, 1))
            if n == 0:
                overlays.append((rect, repl))
    # small images (logos, photos) are blanked; faces/OCR arrive in Phase 3
    images = 0
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            page.add_redact_annot(r, fill=(0.85, 0.85, 0.85))
            images += 1
    if spans or images:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
    if images:
        ctx.quarantine(f"{item} page {page.number + 1} images", "embedded images blanked (face/OCR redaction arrives in Phase 3)")
    for rect, repl in overlays:
        fs = _fit(pymupdf, repl, rect.width, rect.height)
        page.insert_text(pymupdf.Point(rect.x0, rect.y1 - rect.height * 0.2), repl, fontname=FONT, fontsize=fs,
                         color=(0, 0, 0))
    return removed, text


def _scrub_annotations(page, ctx: Context, item: str) -> None:
    pymupdf = _pymupdf()
    for annot in list(page.annots() or []):
        if annot.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
            ctx.quarantine(f"{item} page {page.number + 1} attachment", "file attached to a PDF comment - not exported")
            page.delete_annot(annot)
            continue
        info = annot.info
        new = {}
        if info.get("content"):
            new["content"] = ctx.scrubber.scrub(info["content"], ctx.tally).text
        if info.get("title"):
            ctx.tally.counts["PERSON"] += 1
            new["title"] = ctx.pseudo.token("PERSON", info["title"])
        if info.get("subject"):
            new["subject"] = ctx.scrubber.scrub(info["subject"], ctx.tally).text
        for k in ("creationDate", "modDate"):
            if info.get(k):
                new[k] = ""
        if new:
            annot.set_info(**new)
            annot.update()
    for link in page.get_links():
        if link.get("kind") == pymupdf.LINK_URI and link.get("uri"):
            uri = link["uri"]
            ctx.tally.counts["EMAIL_ADDRESS" if uri.lower().startswith("mailto:") else "URL"] += 1
            link["uri"] = ("mailto:" + ctx.pseudo.email_address(uri[7:]) if uri.lower().startswith("mailto:")
                           else f"https://scrubbed.invalid/{ctx.pseudo.token('URL', uri)}")
            page.update_link(link)
        elif link.get("kind") in (pymupdf.LINK_LAUNCH, pymupdf.LINK_GOTOR):
            page.delete_link(link)
    for widget in list(page.widgets() or []):
        changed = False
        if isinstance(widget.field_value, str) and widget.field_value.strip():
            res = ctx.scrubber.scrub(widget.field_value, ctx.tally)
            if res.findings:
                widget.field_value = res.text
                changed = True
        if widget.choice_values:
            vals = [ctx.scrubber.scrub(str(v), ctx.tally).text if not isinstance(v, (list, tuple)) else v
                    for v in widget.choice_values]
            if vals != list(widget.choice_values):
                widget.choice_values = vals
                changed = True
        if widget.field_label:
            widget.field_label = ctx.scrubber.scrub(widget.field_label, ctx.tally).text
            changed = True
        if changed:
            widget.update()


def _scrub_embedded(doc, ctx: Context, item: str, depth: int) -> None:
    from .dispatch import scrub_bytes

    for name in list(doc.embfile_names()):
        info = doc.embfile_info(name)
        fname = info.get("ufilename") or info.get("filename") or name
        shown = ctx.scrubber.scrub_name(fname, ctx.tally)
        where = f"{item} attachment {shown}"
        data = doc.embfile_get(name)
        try:
            out = scrub_bytes(fname, data, ctx, where, depth=depth)
        except Exception:
            out = None
        doc.embfile_del(name)
        if out is None:
            ctx.quarantine(where, "file embedded in the PDF is not supported - removed")
            continue
        desc = ctx.scrubber.scrub(info.get("description") or "", ctx.tally).text
        doc.embfile_add(shown, out, filename=shown, ufilename=shown, desc=desc)


def _scrub_toc(doc, ctx: Context) -> None:
    toc = doc.get_toc(simple=False)
    if toc:
        titles = ctx.scrubber.scrub_many([t[1] for t in toc], ctx.tally)
        doc.set_toc([[t[0], r.text, t[2], *(t[3:] or [])] for t, r in zip(toc, titles)])


def scrub_pdf_doc(doc, ctx: Context, item: str, depth: int = 0) -> list[str]:
    if doc.needs_pass or doc.is_encrypted:
        raise Quarantined("password-protected PDF - remove the password and re-run")
    for page in doc:
        if _image_share(page) > SCANNED_IMAGE_SHARE:
            raise Quarantined("scanned PDF pages (images of text) - supported in Phase 3 (OCR redaction)")
    removed: list[str] = []
    for page in doc:
        r, _ = scrub_page(page, ctx, item)
        removed += r
        _scrub_annotations(page, ctx, item)
    _scrub_embedded(doc, ctx, item, depth)
    _scrub_toc(doc, ctx)
    doc.set_metadata({})
    doc.del_xml_metadata()
    doc.scrub(attached_files=False, clean_pages=True, embedded_files=False, hidden_text=False, javascript=True,
              metadata=True, redactions=False, redact_images=0, remove_links=False, reset_fields=False,
              reset_responses=True, thumbnails=True, xml_metadata=True)
    ctx.tally.records += doc.page_count
    return removed


def verify_pdf(data: bytes, pages: int, removed: list[str], ctx: Context, item: str) -> None:
    pymupdf = _pymupdf()
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        ctx.issue(f"{item}: scrubbed PDF does not open ({type(exc).__name__})")
        return
    with doc:
        if doc.page_count != pages:
            ctx.issue(f"{item}: page count changed during scrubbing")
        meta = {k: v for k, v in (doc.metadata or {}).items() if v and k not in ("format", "encryption")}
        if meta:
            ctx.issue(f"{item}: metadata survived scrubbing ({sorted(meta)})")
        full = []
        for page in doc:
            text = page.get_text(sort=True)  # by position: tokens drawn in place read in place
            full.append(text)
            ctx.residual(text, f"{item} page {page.number + 1}")
            for annot in page.annots() or []:
                info = annot.info
                ctx.residual("\n\n".join(info.get(k) or "" for k in ("content", "title", "subject")), f"{item} comment")
        joined = "\n".join(full)
        # true redaction: no removed identifier may still be extractable or sit in a raw stream
        raw = b"".join(doc.xref_stream(x) or b"" for x in range(1, doc.xref_length()) if doc.xref_is_stream(x))
        for s in {r.strip() for r in removed if len(r.strip()) >= 5}:
            if s in joined or s.encode("latin-1", "ignore") in raw:
                ctx.issue(f"{item}: removed text is still recoverable from the PDF")
                break


def scrub_pdf_bytes(data: bytes, ctx: Context, item: str, depth: int = 0) -> bytes:
    pymupdf = _pymupdf()
    doc = pymupdf.open(stream=data, filetype="pdf")
    with doc:
        pages = doc.page_count
        removed = scrub_pdf_doc(doc, ctx, item, depth)
        out = doc.tobytes(garbage=4, deflate=True, clean=True)
    verify_pdf(out, pages, removed, ctx, item)
    return out


def process_pdf(src: Path, dst: Path, ctx: Context) -> None:
    out = scrub_pdf_bytes(src.read_bytes(), ctx, ctx.out_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
